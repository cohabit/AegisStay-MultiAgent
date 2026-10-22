"""Small HTTP boundary shared by the deployed Streamlit views."""
from __future__ import annotations

import httpx


class ApiError(RuntimeError):
    pass


class HotelApi:
    def __init__(self, base_url: str, access_token: str, *, client=None):
        self.client = client
        self.base_url = base_url.rstrip("/")
        self.headers = {"Authorization": f"Bearer {access_token}"}

    def request(self, method: str, path: str, **kwargs):
        try:
            response = (self.client.request(method, path, headers=self.headers, **kwargs) if self.client else
                        httpx.request(method, self.base_url + path, headers=self.headers, timeout=10, **kwargs))
        except httpx.HTTPError:
            raise ApiError("暂时无法连接服务，请联系前台或稍后重试。") from None
        if response.status_code >= 400:
            try:
                detail = response.json().get("detail", "请求未完成")
            except ValueError:
                detail = "请求未完成"
            raise ApiError(str(detail))
        return response.json()

    def close(self):
        if self.client:
            self.client.close()
