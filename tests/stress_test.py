"""Async local/cloud concurrency test for the AegisStay FastAPI gateway.

Start the service first::

    python -m uvicorn server:app --host 127.0.0.1 --port 8000

Fast local smoke test (default)::

    python tests/stress_test.py

Cloud model test (uses the API key/base URL configured for the server)::

    python tests/stress_test.py --cloud --sessions 5 --concurrency 3
"""
from __future__ import annotations

import argparse
import asyncio
import math
import os
import statistics
import time
from typing import Any, Sequence

import httpx


DEFAULT_BASE_URL = os.getenv("AEGISSTAY_BASE_URL", "http://127.0.0.1:8000")
DEFAULT_SESSIONS = 10
DEFAULT_CONCURRENCY = 5


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("必须是大于 0 的整数")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="AegisStay 多会话本地/云端并发压测")
    parser.add_argument(
        "--cloud",
        action="store_true",
        help="真实调用服务端配置的云端模型；默认使用 force_rules=True 的本地规则",
    )
    parser.add_argument(
        "--concurrency",
        type=_positive_int,
        default=DEFAULT_CONCURRENCY,
        help=f"同时飞行的 HTTP 请求上限（默认 {DEFAULT_CONCURRENCY}）",
    )
    parser.add_argument(
        "--sessions",
        type=_positive_int,
        default=DEFAULT_SESSIONS,
        help=f"独立会话数量（默认 {DEFAULT_SESSIONS}）",
    )
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help=f"FastAPI 地址（默认 {DEFAULT_BASE_URL}）",
    )
    return parser


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    return build_parser().parse_args(argv)


def percentile(values: list[float], percent: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(percent / 100 * len(ordered)) - 1))
    return ordered[index]


async def request(
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
    method: str,
    path: str,
    **kwargs: Any,
) -> tuple[int, float, dict[str, Any] | None]:
    """Execute one request while strictly bounding in-flight HTTP calls."""
    async with semaphore:
        started = time.perf_counter()
        try:
            response = await client.request(method, path, **kwargs)
            elapsed = time.perf_counter() - started
            try:
                payload = response.json()
            except ValueError:
                payload = None
            return response.status_code, elapsed, payload
        except Exception as exc:  # pragma: no cover - exercised when server is down
            return 0, time.perf_counter() - started, {"error": str(exc)}


async def exercise_session(
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
    index: int,
    *,
    force_rules: bool,
) -> dict[str, Any]:
    """Run a sequential guest workflow inside one isolated session."""
    session_id = f"session_{index:03d}"
    room_no = str(101 + index - 1)
    messages = (
        f"{room_no} 送水",
        f"{room_no} 房间空调不制冷",
        f"{room_no} 能两点退房吗",
        "银河系在哪片星云？",
    )
    observations: list[tuple[int, float]] = []
    model_names: list[str] = []

    for message in messages:
        status, elapsed, payload = await request(
            client,
            semaphore,
            "POST",
            "/api/chat",
            json={
                "session_id": session_id,
                "message": message,
                "force_rules": force_rules,
            },
        )
        observations.append((status, elapsed))
        if payload and payload.get("model_name"):
            model_names.append(str(payload["model_name"]))

    status, elapsed, detail = await request(
        client, semaphore, "GET", f"/api/session/{session_id}"
    )
    observations.append((status, elapsed))

    contamination = 0
    if status != 200 or not detail or detail.get("session_id") != session_id:
        contamination += 1
    if detail:
        if detail.get("room_no") != room_no:
            contamination += 1
        for message in detail.get("messages", []):
            if message.get("room_no") not in {None, room_no}:
                contamination += 1
        for ticket in detail.get("tickets", []):
            if ticket.get("room_no") != room_no:
                contamination += 1

    return {
        "observations": observations,
        "contamination": contamination,
        "model_names": model_names,
    }


async def run_stress(args: argparse.Namespace) -> dict[str, Any]:
    started = time.perf_counter()
    semaphore = asyncio.Semaphore(args.concurrency)
    limits = httpx.Limits(
        max_connections=max(args.concurrency, args.sessions),
        max_keepalive_connections=args.sessions,
    )
    async with httpx.AsyncClient(base_url=args.base_url, timeout=30.0, limits=limits) as client:
        try:
            await client.post("/api/admin/reset")
        except Exception:
            pass
        results = await asyncio.gather(
            *(
                exercise_session(
                    client,
                    semaphore,
                    index,
                    force_rules=not args.cloud,
                )
                for index in range(1, args.sessions + 1)
            )
        )
    wall_seconds = max(time.perf_counter() - started, 1e-9)
    observations = [item for result in results for item in result["observations"]]
    latencies = [elapsed for _, elapsed in observations]
    ok_count = sum(status == 200 for status, _ in observations)
    contamination = sum(result["contamination"] for result in results)
    model_names = sorted({name for result in results for name in result["model_names"]})
    if not model_names or not args.cloud:
        model_names = [
            os.getenv("OPENAI_MODEL", "Qwen/Qwen2.5-7B-Instruct")
            if args.cloud
            else "规则引擎 (force_rules=True)"
        ]
    total = len(observations)
    return {
        "mode": "云端大模型真实并发压测" if args.cloud else "本地规则微服务架构压测",
        "model_name": ", ".join(model_names),
        "sessions": args.sessions,
        "concurrency": args.concurrency,
        "total": total,
        "ok_count": ok_count,
        "wall_seconds": wall_seconds,
        "qps": total / wall_seconds,
        "avg_latency_ms": statistics.mean(latencies) * 1000 if latencies else 0.0,
        "p95_latency_ms": percentile(latencies, 95) * 1000,
        "p99_latency_ms": percentile(latencies, 99) * 1000,
        "contamination": contamination,
    }


def print_report(report: dict[str, Any]) -> None:
    total = report["total"] or 1
    print("AegisStay Async Stress Report")
    print("=" * 68)
    print(f"Mode: {report['mode']}")
    print(f"Model: {report['model_name']}")
    print(f"Sessions: {report['sessions']} | Concurrency limit: {report['concurrency']}")
    print(f"Total requests: {report['total']}")
    print(f"HTTP 200 success: {report['ok_count']}/{total} ({report['ok_count'] / total * 100:.2f}%)")
    print(f"Throughput QPS: {report['qps']:.2f}")
    print(f"Avg Latency: {report['avg_latency_ms']:.2f} ms")
    print(f"P95 Latency: {report['p95_latency_ms']:.2f} ms")
    print(f"P99 Latency: {report['p99_latency_ms']:.2f} ms")
    print(
        "Cross-session contamination: "
        f"{report['contamination']} ({'PASS' if report['contamination'] == 0 else 'FAIL'})"
    )


async def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    report = await run_stress(args)
    print_report(report)


if __name__ == "__main__":
    asyncio.run(main())
