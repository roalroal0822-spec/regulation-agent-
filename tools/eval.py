"""정확도 성적표: 모델·인터넷 없이 같은 결과가 나오는 시험을 돌려 검증결과/에 남긴다.

  python tools/eval.py [--note "무엇을 고친 뒤 쟀는지"]  → 검증결과/scorecard.json, 검증결과/정확도_성적표.md

시험 다섯 가지
  1. 개정 의도 해석: 시연 규정집에 대한 개정 요청 문장(A 12개는 고칠 때 써도 되는 묶음, B 12개는 고칠 때 보지 않은 묶음)을
     개정안으로 만들어, 고쳐진 조문에 정답 글자가 들어가고 옛 글자가 빠졌는지 본다.
  2. 전체 점검: 무작위 규정 30개(시드 고정)에 빠진 조 번호·없는 조 인용·용어 불일치·없는 별표 인용·규정집에 없는 규정 인용을
     몰래 심고, 심은 것만 정확히 찾는지(놓침·오탐 모두 실패) 본다. 바른 인용·바른 용어는 '잡으면 안 되는 것'으로 함께 넣는다.
  3. 번호 이동 인용 정정: 무작위 규정 30개에 조를 하나 신설해 뒤 조 번호를 밀었을 때, 모든 조문 인용이 새 번호를 가리키는지 본다
     (정답 번호는 이 시험이 따로 계산).
  4. 모델 감시(가짜 모델): 없는 조·현행에 없는 글자·개정 의도에 없는 숫자를 낸 작업은 버리고, 맞는 작업은 쓰는지 본다.
  5. 신원·금융 정보 가림: 요청 문장의 주민번호·계좌 등은 가리고, 규정 문장(조 번호·날짜·금액·전화)은 건드리지 않는지 본다.
문항과 정답은 이 파일에 있고, 틀린 문항은 고치지 않고 성적표에 그대로 적는다.
"""
import json
import os
import random
import re
import sys
import tempfile
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["APP_DATA_DIR"] = tempfile.mkdtemp(prefix="reg-eval-")
os.environ["LLM_PROVIDER"] = "none"

import amend  # noqa: E402
import app  # noqa: E402
import lint  # noqa: E402
from core.pii import SENSITIVE, mask  # noqa: E402
from library import Library  # noqa: E402

# ------------------------------------------------------------ 1. 개정 의도 해석
# (요청, 규정 이름, [(확인할 조, 새 글자에 있어야 할 것, 없어야 할 것)] 또는 ("insert", 앞 조, 제목 낱말) / ("delete", 조) / ("retitle", 조, 새 제목)

INTENT_A = [
    ("체육시설 운영 규정에서 대관 사용료 납부 기한을 7일에서 5일로 줄여줘", "체육시설 운영 규정", [("8", "허가를 받은 날부터 5일 이내", "허가를 받은 날부터 7일")]),
    ("체육시설 운영 규정 운영 시간을 22:00에서 21:00로 바꿔줘", "체육시설 운영 규정", [("3", "06:00부터 21:00까지", "22:00")]),
    ("체육시설 운영 규정 단체 이용 신청을 이용 예정일 7일 전에서 10일 전으로 늘려줘", "체육시설 운영 규정", [("5", "이용 예정일 10일 전까지", "이용 예정일 7일 전")]),
    ("체육시설 운영 규정에서 대관 신청 기한을 14일 전에서 20일 전으로 바꿔줘", "체육시설 운영 규정", [("6", "사용 예정일 20일 전까지", "14일 전")]),
    ("체육시설 운영 규정 제8조 다음에 사용허가 취소에 관한 조문을 신설해줘", "체육시설 운영 규정", [("insert", "8", "취소")]),
    ("체육시설 운영 규정 제12조를 삭제해줘", "체육시설 운영 규정", [("delete", "12")]),
    ("공영주차장 운영 규정에서 정기주차 신청 기한을 10일 전에서 7일 전으로 바꿔줘", "공영주차장 운영 규정", [("4", "이용 시작일 7일 전까지", "10일 전")]),
    ("공영주차장 운영 규정 정기주차 요금 납부 기한을 5일에서 7일로 늘려줘", "공영주차장 운영 규정", [("6", "7일 이내에 납부", "5일 이내")]),
    ("회계규정에서 여비 정산 기한을 5일에서 7일로 바꿔줘", "회계규정", [("4", "7일 이내", "5일 이내")]),
    ("체육시설 운영 규정 제13조의 제목을 '손해배상 책임'으로 바꿔줘", "체육시설 운영 규정", [("retitle", "13", "손해배상 책임")]),
    ("체육시설 운영 규정 안전점검을 매월 1회 이상에서 매월 2회 이상으로 강화해줘", "체육시설 운영 규정", [("11", "매월 2회 이상", "매월 1회")]),
    ("체육시설 운영 규정 허가 여부 통지 기한을 5일에서 3일로 줄여줘", "체육시설 운영 규정", [("6", "3일 이내에 허가 여부", "5일 이내에 허가")]),
]
INTENT_B = [
    ("체육시설 운영 규정에서 휴관 안내를 7일 전에서 10일 전으로 바꿔줘", "체육시설 운영 규정", [("3", "10일 전까지 누리집", "7일 전까지")]),
    ("공영주차장 운영 규정의 운영 시간을 연중 24시간에서 06:00부터 24:00까지로 바꿔", "공영주차장 운영 규정", [("3", "06:00부터 24:00까지", "연중 24시간")]),
    ("체육시설 운영 규정 반환 기준 3일 전을 5일 전으로 바꿔줘", "체육시설 운영 규정", [("9", "5일 전까지 취소", "3일 전까지")]),
    ("체육시설 운영 규정 100분의 90을 100분의 80으로 낮춰줘", "체육시설 운영 규정", [("9", "100분의 80", "100분의 90")]),
    ("공영주차장 운영 규정 제8조 다음에 이용자 준수사항 조문을 신설해줘", "공영주차장 운영 규정", [("insert", "8", "준수사항")]),
    ("회계규정 제6조를 삭제해줘", "회계규정", [("delete", "6")]),
    ("체육시설 운영 규정 제10조 제목을 '사용료의 감면 기준'으로 바꿔줘", "체육시설 운영 규정", [("retitle", "10", "사용료의 감면 기준")]),
    ("체육시설 운영 규정 정기 휴관일을 매월 첫째 주 월요일에서 매월 둘째 주 월요일로 바꿔", "체육시설 운영 규정", [("4", "둘째 주 월요일", "첫째 주")]),
    ("공영주차장 운영 규정 제7조를 삭제해줘", "공영주차장 운영 규정", [("delete", "7")]),
    ("체육시설 운영 규정 대관 사용료 납부 기한을 일주일에서 열흘로 늘려줘", "체육시설 운영 규정", [("8", "10일 이내에 납부", "7일 이내에 납부")]),
    ("회계규정 회계 사무 총괄을 회계팀장에서 재무팀장으로 바꿔줘", "회계규정", [("7", "재무팀장이 총괄", "회계팀장")]),
    ("체육시설 운영 규정 제11조 다음에 시설 개방에 관한 조문을 신설해줘", "체육시설 운영 규정", [("insert", "11", "개방")]),
]


def entry(name):
    return next(e for e in app.lib().regs() if e.short == name)


def check_intent(text, name, wants, llm=None):
    e = entry(name)
    p = amend.plan(text, e, app.lib(), llm, "이사장")
    if not p["ops"]:
        return "개정 작업을 만들지 못함: " + " ".join(p["notes"])[:80]
    ops = amend.deep(p["ops"])
    for o in ops:  # 여러 곳 후보가 있어도 1순위로 적용해 본다(화면에서는 고르게 함)
        o.pop("alternatives", None)
    res = amend.apply(e, ops, "branch", None, app.lib())
    if res["errors"]:
        return "적용 오류: " + "; ".join(map(str, res["errors"]))[:80]
    new = res["reg"]
    errs = []
    for w in wants:
        if w[0] == "insert":
            keys = [a.key for a in new.articles]
            i = keys.index(w[1]) if w[1] in keys else -1
            nxt = new.articles[i + 1] if 0 <= i < len(new.articles) - 1 else None
            if not nxt or w[2] not in (nxt.title or "") or nxt.key in {a.key for a in entry(name).reg.articles}:
                errs.append(f"제{w[1]}조 다음에 '{w[2]}' 조가 없음")
        elif w[0] == "delete":
            if not any(o["type"] == "delete" and o["article"] == w[1] for o in ops):
                errs.append(f"제{w[1]}조 삭제 작업 없음")
        elif w[0] == "retitle":
            a = new.get(w[1])
            if not a or a.title != w[2]:
                errs.append(f"제{w[1]}조 제목이 '{a.title if a else '-'}'")
        else:
            a = new.get(w[0])
            txt = a.to_text() if a else ""
            if w[1] not in txt:
                errs.append(f"제{w[0]}조에 '{w[1]}' 없음")
            if w[2] and w[2] in txt:
                errs.append(f"제{w[0]}조에 옛 글자 '{w[2]}'가 남음")
    others = [o for o in ops if (o.get("article") or o.get("after")) not in {w[1] if w[0] in ("insert", "delete", "retitle") else w[0] for w in wants}]
    if others:
        errs.append("요청에 없는 조까지 바꿈: " + ", ".join(str(o.get("article") or o.get("after")) for o in others))
    return "; ".join(errs) or None


def eval_intents():
    out = []
    for nm, cases, desc in (("개정 의도 해석 A(개선에 쓴 문장)", INTENT_A, "개정 요청 문장으로 만든 개정안에서 고친 조문에 정답 글자가 있고 옛 글자가 빠졌으며, 요청에 없는 조는 건드리지 않은 비율"),
                            ("개정 의도 해석 B(개선에 쓰지 않은 문장)", INTENT_B, "A와 다른 요청. 앱을 고칠 때 보지 않고 한 번만 잼")):
        ok, fails = 0, []
        for text, name, wants in cases:
            e = check_intent(text, name, wants)
            if e:
                fails.append(f"'{text}' → {e}")
            else:
                ok += 1
        out.append({"name": nm, "passed": ok, "total": len(cases), "failures": fails, "desc": desc})
    return out


# ------------------------------------------------------------ 2·3. 무작위 규정

BODIES = [("목적", "이 지침은 기관이 운영하는 {x}의 관리에 필요한 사항을 정함을 목적으로 한다."),
          ("정의", "이 지침에서 \"이용자\"란 {x}을 이용하는 사람을 말한다."),
          ("운영 시간", "{x}의 운영 시간은 09:00부터 18:00까지로 한다."),
          ("이용 신청", "{x}을 이용하려는 사람은 이용 예정일 3일 전까지 신청하여야 한다."),
          ("이용 제한", "이사장은 시설 보존에 지장이 있는 경우 이용을 제한할 수 있다."),
          ("사용료", "사용료는 1시간당 10,000원으로 한다."),
          ("사용료의 납부", "사용료는 허가를 받은 날부터 5일 이내에 납부하여야 한다."),
          ("반환", "이미 낸 사용료는 반환하지 아니한다."),
          ("안전관리", "이사장은 매월 1회 이상 {x}의 안전점검을 실시하여야 한다."),
          ("이용자 준수사항", "이용자는 직원의 안내에 따라야 한다."),
          ("손해배상", "이용자가 시설을 훼손한 경우에는 그 손해를 배상하여야 한다."),
          ("공고", "이사장은 이용 안내를 누리집에 게시하여야 한다."),
          ("위임", "이 지침에서 정하지 아니한 사항은 이사장이 따로 정한다.")]
PLACES = ["주민 문화교실", "다목적 강당", "공공 텃밭", "청소년 독서실", "실내 암벽장", "어린이 놀이방", "공유 회의실", "야외 공연장"]


def make_reg(rnd, plants):
    """조 목록 [(번호, 제목, 본문)]과 심은 오류 종류. plants: 심을 오류 종류 목록."""
    x = rnd.choice(PLACES)
    n = rnd.randint(8, 12)
    arts = [[i + 1, t, b.format(x=x)] for i, (t, b) in enumerate(BODIES[:n])]
    # 바른 인용(잡으면 안 됨): 뒤쪽 조에서 앞쪽 조를 인용
    for _ in range(2):
        i = rnd.randint(4, n - 1)
        j = rnd.randint(2, i - 1)
        arts[i][2] += f" 이 경우 제{j}조에 따른다."
    planted = set()
    if "없는 조 인용" in plants:
        i = rnd.randint(2, n - 1)
        arts[i][2] += f" 다만, 제{n + rnd.randint(2, 5)}조의 경우는 예외로 한다."
        planted.add("인용 오류")
    if "용어" in plants:  # 같은 규정 안에서 '누리집'과 '홈페이지'를 섞어 씀(한쪽만 쓰면 불일치가 아님)
        i, j = rnd.sample(range(1, n), 2)
        arts[i][2] += " 이 내용은 홈페이지에도 알린다."
        if not any("누리집" in a[2] for a in arts):
            arts[j][2] += " 결과는 누리집에 게시한다."
        planted.add("용어 불일치")
    if "별표" in plants:
        i = rnd.randint(1, n - 1)
        arts[i][2] += " 세부 기준은 별표 2와 같다."
        planted.add("별표 없음")
    if "없는 규정" in plants:
        i = rnd.randint(1, n - 1)
        arts[i][2] += " 그 밖의 사항은 「시설물 대여 운영 세칙」에 따른다."
        planted.add("인용 규정 없음")
    if "빠진 조" in plants:
        k = rnd.randint(3, n - 2)
        # 빠진 조를 인용하는 곳이 없게 바른 인용을 다시 맞춘다
        for a in arts:
            a[2] = re.sub(r"제(\d+)조에 따른다", lambda m: f"제{m.group(1)}조에 따른다" if int(m.group(1)) != k else "제1조에 따른다", a[2])
        arts = [a for a in arts if a[0] != k]
        planted.add("조 번호")
    return x, arts, planted


def reg_text(x, arts):
    return f"공공기관 {x} 운영 지침\n" + "\n".join(f"제{no}조({t}) {b}" for no, t, b in arts) + "\n부칙\n이 지침은 2026년 1월 1일부터 시행한다.\n"


def make_lib(files):
    root = Path(tempfile.mkdtemp(prefix="reg-lib-"))
    (root / "org").mkdir(parents=True)
    for name, text in files.items():
        (root / "org" / name).write_text(text, encoding="utf-8")
    return Library(root, "공공기관").load()


def eval_lint():
    rnd = random.Random(20261015)
    kinds = ["없는 조 인용", "용어", "별표", "없는 규정", "빠진 조"]
    ok, fails, n_planted, n_found = 0, [], 0, 0
    for w in range(30):
        plants = rnd.sample(kinds, rnd.randint(0, 2))
        x, arts, planted = make_reg(rnd, plants)
        L = make_lib({f"{x} 운영 지침.txt": reg_text(x, arts)})
        r = lint.check(L, app.settings().get("terms"))
        got = {i["kind"] for i in r["issues"] if i["level"] in ("오류", "주의")}
        n_planted += len(planted)
        n_found += len(planted & got)
        if got == planted:
            ok += 1
        else:
            miss, extra = planted - got, got - planted
            fails.append(f"규정{w + 1} " + (f"놓침 {', '.join(sorted(miss))} " if miss else "") + (f"오탐 {', '.join(sorted(extra))}" if extra else "")
                         + (" (심은 것 없음)" if not planted else ""))
    return [{"name": "전체 점검(무작위 규정)", "passed": ok, "total": 30, "failures": fails,
             "desc": f"무작위 규정 30개에 심은 오류 {n_planted}건(빠진 조 번호·없는 조 인용·용어 불일치·없는 별표·없는 규정 인용, 0~2건씩)을 빠짐없이, "
                     f"심지 않은 것은 잡지 않은 규정 비율 (심은 오류 단위로는 {n_found}/{n_planted} 찾음)"}]


def eval_renumber():
    rnd = random.Random(7)
    ok, fails, n_refs = 0, [], 0
    for w in range(30):
        x, arts, _ = make_reg(rnd, [])
        n = len(arts)
        for _ in range(3):  # 인용을 더 심는다(앞·뒤 어느 쪽이든)
            i, j = rnd.randrange(n), rnd.randrange(n)
            if i != j:
                arts[i][2] += f" 제{arts[j][0]}조제1항에 따른 경우도 같다." if rnd.random() < 0.3 else f" 제{arts[j][0]}조를 준용한다."
        L = make_lib({f"{x} 운영 지침.txt": reg_text(x, arts)})
        e = L.regs()[0]
        k = rnd.randint(2, n - 2)
        op = {"type": "insert", "after": str(k), "title": "시설 개방", "text": "이사장은 휴관일에도 시설을 개방할 수 있다."}
        res = amend.apply(e, [op], "renumber", None, L)
        new = res["reg"]
        newmap = lambda j: j if j <= k else j + 1  # 정답: 신설 조 뒤로 번호가 하나씩 밀림
        errs = []
        if [a.no for a in new.articles] != list(range(1, n + 2)):
            errs.append(f"조 번호가 1~{n + 1}로 이어지지 않음")
        for no, t, b in arts:
            want_refs = [newmap(int(j)) for j in re.findall(r"제(\d+)조", b)]
            n_refs += len(want_refs)
            a = new.get(str(newmap(no)))
            got = [int(j) for j in re.findall(r"제(\d+)조", a.to_text().split(")", 1)[1])] if a else []
            if got != want_refs:
                errs.append(f"옛 제{no}조(새 제{newmap(no)}조) 인용 {got} (정답 {want_refs})")
        if errs:
            fails.append(f"규정{w + 1}(제{k}조 다음 신설): " + "; ".join(errs[:2]))
        else:
            ok += 1
    return [{"name": "번호 이동 인용 정정", "passed": ok, "total": 30, "failures": fails,
             "desc": f"무작위 규정 30개에 조를 신설해 번호를 밀었을 때 조 번호가 이어지고 조문 인용 {n_refs}곳이 모두 새 번호를 가리킨 규정 비율"}]


# ------------------------------------------------------------ 4. 모델 감시

class FakeLLM:
    enabled = True
    label = "가짜 모델"

    def __init__(self, ops):
        self.ops = ops

    def json(self, system, user, **kw):
        return {"operations": self.ops, "effective": ""}


def eval_model_guard():
    e = entry("체육시설 운영 규정")
    intent = "체육시설 운영 규정에서 대관 사용료 납부 기한을 7일에서 5일로 줄여줘"
    bad_cases = [
        ("없는 조", {"type": "replace", "article": "30", "find": "7일", "repl": "5일"}),
        ("현행에 없는 글자", {"type": "replace", "article": "8", "find": "10일 이내", "repl": "5일 이내"}),
        ("의도에 없는 숫자", {"type": "replace", "article": "8", "find": "7일 이내", "repl": "3일 이내"}),
        ("의도에 없는 숫자(금액)", {"type": "replace", "article": "8", "find": "7일 이내", "repl": "5일 이내 50,000원"}),
        ("형식 오류", {"type": "move_all", "article": "8"}),
        ("없는 조 뒤 신설", {"type": "insert", "after": "40", "title": "가짜 조"}),
    ]
    ok, fails = 0, []
    for name, op in bad_cases:
        p = amend.plan(intent, e, app.lib(), FakeLLM([op]), "이사장")
        used = [o for o in p["ops"] if o.get("src") == "모델"]
        if not used:
            ok += 1
        else:
            fails.append(f"{name}: 모델 작업이 쓰임 {used[0]}")
    good_ok, good_fail = 0, []
    goods = [(intent, {"type": "replace", "article": "8", "find": "7일 이내", "repl": "5일 이내"}, ("8", "허가를 받은 날부터 5일 이내")),
             ("체육시설 운영 규정 운영 시간을 22:00에서 21:00로 바꿔줘", {"type": "replace", "article": "3", "find": "22:00", "repl": "21:00"}, ("3", "06:00부터 21:00까지")),
             ("체육시설 운영 규정 제13조 제목을 '손해배상 책임'으로 바꿔줘", {"type": "retitle", "article": "13", "title": "손해배상 책임"}, None)]
    for text, op, want in goods:
        p = amend.plan(text, e, app.lib(), FakeLLM([op]), "이사장")
        used = [o for o in p["ops"] if o.get("src") == "모델"]
        if used:
            good_ok += 1
        else:
            good_fail.append(f"'{text}' 맞는 모델 작업을 버림: {' '.join(p['notes'])[:80]}")
    return [{"name": "모델 작업 걸러내기", "passed": ok, "total": len(bad_cases), "failures": fails,
             "desc": "가짜 모델이 없는 조, 현행에 없는 글자, 개정 의도에 없는 숫자, 형식 오류 작업을 냈을 때 쓰지 않은 비율"},
            {"name": "맞는 모델 작업은 씀", "passed": good_ok, "total": len(goods), "failures": good_fail,
             "desc": "현행 글자·의도와 맞는 모델 작업을 버리지 않은 비율"}]


# ------------------------------------------------------------ 5. 가림

SENS_POS = ["강사 주민번호 850315-2345678 적어줘", "계좌 110-234-567890로 환불", "카드 4321-8765-1234-5678 결제분", "외국인등록번호 900101-5123456",
            "여권번호 M12345678", "국민은행 123456-12-345678", "신한 110-12-345678 입금", "주민 9001011234567", "농협 301-1234-5678-91",
            "주민등록 760505 1234567", "카드번호 1111 2222 3333 4444", "우리 1002-345-678901", "여권 S98765432", "계좌 3333-01-1234567", "반환 계좌 352-0123-4567-83"]


def eval_pii():
    ok, fails = 0, []
    for t in SENS_POS:
        out, kinds = mask(t, SENSITIVE)
        if kinds and not re.search(r"\d{4,}", out.replace(" ", "")):
            ok += 1
        else:
            fails.append(f"'{t}' → '{out}'")
    neg = set()
    for e in app.lib().regs():
        for a in e.reg.articles:
            neg.update(x for x in a.to_text().split("\n") if x.strip())
    for t, _, _ in INTENT_A + INTENT_B:
        neg.add(t)
    neg = sorted(neg)
    n_ok, n_fail = 0, []
    for t in neg:
        _, kinds = mask(t, SENSITIVE)
        if kinds:
            n_fail.append(f"잘못 가림({', '.join(kinds)}): {t[:60]}")
        else:
            n_ok += 1
    return [{"name": "신원·금융 번호 가림", "passed": ok, "total": len(SENS_POS), "failures": fails,
             "desc": "주민번호·외국인등록번호·계좌·카드·여권 번호가 든 요청 문장 15개를 가린 비율"},
            {"name": "규정 문장 오탐 없음", "passed": n_ok, "total": len(neg), "failures": n_fail,
             "desc": "시연 규정집의 모든 조문 줄과 요청 문장(조 번호·날짜·시각·금액 포함)을 잘못 가리지 않은 비율"}]


def main():
    import argparse
    ap = argparse.ArgumentParser(description="정확도 성적표 만들기")
    ap.add_argument("--note", default="", help="측정 이력에 남길 한 줄(무엇을 고친 뒤 쟀는지)")
    note = ap.parse_args().note
    t0 = time.time()
    areas = eval_intents() + eval_lint() + eval_renumber() + eval_model_guard() + eval_pii()
    out = ROOT / "검증결과"
    out.mkdir(exist_ok=True)
    prev = None
    try:
        prev = json.loads((out / "scorecard.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    summary = ", ".join(f"{a['name']} {a['passed']}/{a['total']}" for a in areas)
    hist = (prev or {}).get("history", [])
    if not hist or hist[-1]["summary"] != summary or note:
        hist.append({"date": date.today().isoformat(), "summary": summary, "note": note})
    sc = {"app": "규정 제·개정 에이전트", "date": date.today().isoformat(), "command": "python tools/eval.py",
          "author": "개발자 작성(블라인드 아님) · 무작위 규정(시드 고정)과 A 문장은 앱을 고칠 때 봤고, B 문장만 보지 않음", "seconds": round(time.time() - t0, 1),
          "areas": areas, "history": hist[-10:]}
    (out / "scorecard.json").write_text(json.dumps(sc, ensure_ascii=False, indent=1), encoding="utf-8")
    md = ["# 정확도 성적표 (규정 제·개정 에이전트)", "", f"- 측정일: {sc['date']} · 재현: `{sc['command']}` (모델·인터넷 불필요, {sc['seconds']}초)",
          f"- 문항: {sc['author']}", "", "| 시험 | 결과 | 무엇을 쟀나 |", "|---|---|---|"]
    for a in areas:
        pct = f" ({a['passed'] / a['total'] * 100:.1f}%)" if a["total"] else ""
        md.append(f"| {a['name']} | {a['passed']}/{a['total']}{pct} | {a['desc']} |")
    md += ["", "## 틀린 문항 (고치지 않고 공개)", ""]
    fails = [(a["name"], f) for a in areas for f in a["failures"]]
    md += [f"- [{n}] {f}" for n, f in fails] or ["- 없음"]
    md += ["", "## 측정 이력", ""] + [f"- {i}차 {h['date']}" + (f" ({h['note']})" if h.get("note") else "") + f": {h['summary']}" for i, h in enumerate(hist[-10:], 1)]
    (out / "정확도_성적표.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print("\n".join(md[:8 + len(areas)]))
    print(f"\n틀린 문항 {len(fails)}건 · 결과: {out / '정확도_성적표.md'}")


if __name__ == "__main__":
    main()
