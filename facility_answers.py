"""Question-scoped facility answers from explicit, profile-local approved fields."""
from __future__ import annotations

import re


def topic_scopes(text: str, topics: list[dict]) -> dict[str, str]:
    """Keep each mention's questions separate, including unpunctuated Chinese."""
    mentions = []
    for topic in topics:
        for alias in topic["aliases"]:
            pattern = r"\b" + re.escape(alias) + r"\b" if alias.isascii() else re.escape(alias)
            mentions.extend((match.start(), match.end(), topic["topic"])
                            for match in re.finditer(pattern, text, re.IGNORECASE))
    chosen = []
    for mention in sorted(mentions, key=lambda value: (value[0], -(value[1] - value[0]))):
        if not chosen or mention[0] >= chosen[-1][1]:
            chosen.append(mention)
    scopes = []
    carried = ""
    for index, (start, end, topic) in enumerate(chosen):
        stop = chosen[index + 1][0] if index + 1 < len(chosen) else len(text)
        suffix = text[end:stop]
        # A location question before the next noun belongs to that next noun.
        next_prefix = re.search(r"(?:在哪(?:里|儿)?|哪里|哪儿|什么地方)(?:可以|能)?$", suffix) if index + 1 < len(chosen) else None
        if next_prefix and not (question_fields(suffix[:next_prefix.start()])
                                or re.search(r"可以|能", next_prefix.group())):
            next_prefix = None
        prefix = (text[:start] if index == 0 else carried)
        carried = next_prefix.group() if next_prefix else ""
        if next_prefix:
            suffix = suffix[:next_prefix.start()]
        scopes.append([topic, prefix + suffix])
    # Lists such as 健身房和泳池几点关门 share the trailing question.
    for index in range(len(scopes) - 2, -1, -1):
        if re.fullmatch(r"[\s、和与及还有，,]*", scopes[index][1]):
            scopes[index][1] = scopes[index + 1][1]
    result = {}
    for topic, scope in scopes:
        result[topic] = result.get(topic, "") + " " + scope
    return result


def question_fields(scope: str) -> list[str]:
    # Discard explicitly rejected questions, retaining the positive correction.
    scope = re.sub(r"(?:不是问|不问|不用告诉我|不需要(?:知道|了解)?)[^，,。；;]*?(?=[，,。；;]|只问|是问|$)", "", scope)
    wanted = []
    patterns = {
        "location": r"在哪|哪里|哪儿|位置|地点|几楼|几层|什么地方",
        "opens_at": r"开门|开馆|开始营业|开始开放|几点开放|何时开放|什么时候开放",
        "closes_at": r"关门|关闭|闭馆|打烊|停止营业|结束营业|营业结束|几点结束|何时结束",
        "hours": r"开放时间|开放时段|营业时间|营业时段|开放到|从几点到几点",
        "price": r"收费|费用|多少钱|价格|免费",
        "reservation": r"预约|预订|预定",
        "availability": r"有没有|是否有|能不能|可以吗|提供吗|有吗",
    }
    for field, pattern in patterns.items():
        if re.search(pattern, scope):
            wanted.append(field)
    return wanted


def answer_facility(topic: str, fields: dict, scope: str, fallback: str) -> str:
    wanted = question_fields(scope)
    if not wanted:
        return fallback
    labels = {"location": "位置", "opens_at": "开门时间", "closes_at": "关门时间",
              "hours": "开放时段", "price": "费用", "reservation": "预约方式", "availability": "可用情况"}
    replies = []
    for field in wanted:
        value = fields.get(field)
        if field == "hours" and not value and fields.get("opens_at") and fields.get("closes_at"):
            value = fields["opens_at"] + "–" + fields["closes_at"]
        if not isinstance(value, str) or not value.strip():
            replies.append(f"{topic}的{labels[field]}尚未核验，请联系前台确认。")
        elif field == "location":
            replies.append(f"{topic}位于{value}。")
        elif field in {"opens_at", "closes_at"}:
            replies.append(f"{topic} {value} {'开门' if field == 'opens_at' else '关门'}。")
        else:
            replies.append(f"{topic}{labels[field]}：{value}。")
    return " ".join(replies)
