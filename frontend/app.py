"""CitizenAssist AI: Streamlit front end.

Run the API first:   python -m uvicorn app.main:create_app --factory --reload
Then this app:       python -m streamlit run frontend/app.py

Every fact on screen comes from the API. Answers a person types are kept only in this browser
session's memory and are sent only to the API for evaluation; nothing is written to disk.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st  # noqa: E402

from frontend import api_client  # noqa: E402
from frontend.api_client import ApiProblem, ApiUnavailable  # noqa: E402
from frontend.ui_helpers import (STATUS_ICON, build_profile, collect_sources, format_value,  # noqa: E402
                                 group_results, question_priorities, rule_icon, tree_lines)

PAGES = ["Browse schemes", "Check my eligibility", "About"]
CHAT = "Chat"
NATIVE = {"en": "English", "hi": "हिन्दी", "mr": "मराठी"}


# ------------------------------------------------------------------------------------ shared
def ui_text(lang: str) -> dict:
    """Interface strings for the chosen language, fetched from the API (one wording source)."""
    cache = st.session_state.setdefault("ui_cache", {})
    if lang not in cache:
        cache[lang] = api_client.call("GET", f"/api/v1/ui/{lang}")["ui"]
    return cache[lang]


def guarded(fn, t: dict | None = None):
    """Run a page; turn API trouble into a plain-language message instead of a traceback."""
    t = t or ui_text("en")
    try:
        fn()
    except ApiUnavailable:
        st.error(t["service_down"])
    except ApiProblem as e:
        if e.code == "database_not_ready":
            st.error(t["db_not_ready"])
        else:
            st.error(t["service_problem"].format(code=e.code, message=e.message))


def notice(text: str) -> None:
    st.info(text, icon="ℹ️")


# ------------------------------------------------------------------------------------ browse
def page_browse(lang: str, t: dict) -> None:
    st.header(t["browse_title"])
    if st.session_state.get("selected_scheme"):
        return show_detail(st.session_state["selected_scheme"], lang, t)

    fields = {f["key"]: f for f in api_client.call("GET", "/api/v1/fields", params={"lang": lang})["fields"]}
    states = fields["state_code"]["value_labels"]
    level_name = {"central": t["level_central"], "state": t["level_state"], "ut": t["level_ut"], "local": t["level_local"]}
    c1, c2, c3 = st.columns([3, 1, 2])
    q = c1.text_input(t["browse_search"], key="browse_q", max_chars=100)
    level = c2.selectbox(t["browse_level"], ["All", "central", "state"],
                         format_func=lambda x: t["browse_all"] if x == "All" else level_name[x], key="browse_level")
    state = c3.selectbox(t["browse_state"], [None] + list(states),
                         format_func=lambda c: t["browse_all_states"] if c is None else states[c],
                         key="browse_state", help=t["browse_state_help"])
    params = {k: v for k, v in {"q": q.strip() or None, "level": None if level == "All" else level,
                                "state": state, "lang": lang}.items() if v}
    data = api_client.call("GET", "/api/v1/schemes", params=params)
    st.caption(t["browse_count"].format(n=data["count"]))
    if not data["count"]:
        st.warning(t["browse_none"])
    for s in data["schemes"]:
        with st.container(border=True):
            st.subheader(s["name"])
            st.caption(" · ".join([t["level_line"].format(level=level_name.get(s["level"], s["level"])),
                                   t.get("status_" + s["status"], s["status"]),
                                   t["verification"].get(s["verification_status"], s["verification_status"])]))
            if st.button(t["view_details"], key=f"view_{s['scheme_id']}"):
                st.session_state["selected_scheme"] = s["scheme_id"]
                st.rerun()


def show_detail(scheme_id: str, lang: str, t: dict) -> None:
    if st.button(t["back"], key="back"):
        st.session_state["selected_scheme"] = None
        st.rerun()
    try:
        d = api_client.call("GET", f"/api/v1/schemes/{scheme_id}", params={"lang": lang})
    except ApiProblem as e:
        if e.code == "scheme_not_found":
            st.session_state["selected_scheme"] = None
            st.warning(t["scheme_not_found"])
            return
        raise
    tr, rec = d["translation"], d["record"]
    tree_t = {"all": t["tree_all"], "any": t["tree_any"], "not_checkable": t["not_checkable"],
              "source": t["source_line"].split("{")[0].strip()}
    src = lambda loc: t["source_line"].format(locator=loc)  # noqa: E731
    st.subheader(tr["name"] if tr else d["scheme_id"])
    badges = [t["verification"].get(rec["verification_status"], rec["verification_status"]),
              t.get("status_" + rec["status"], rec["status"])]
    if rec["is_stale"]:
        badges.append(t["overdue"])
    st.caption(" · ".join(badges))
    if tr and tr.get("language") and tr["language"] != lang:
        st.caption(f"{t['english_shown']}")
    if rec["last_verified"]:
        st.caption(t["last_checked"].format(date=rec["last_verified"]))
    if not rec["rules_complete"]:
        st.warning(t["rules_incomplete_warn"])
    if rec["is_stale"]:
        st.warning(t["stale_warn"])
    if tr:
        for title, key in ((t["what_it_is"], "short_description"), (t["benefits"], "benefits"), (t["who_for"], "target_beneficiaries")):
            if tr.get(key):
                st.markdown(f"**{title}**")
                st.write(tr[key])
    st.caption(" · ".join(x for x in (d.get("ministry"), d.get("department")) if x))

    st.markdown(f"### {t['eligibility_rules']}")
    lines = tree_lines(d["eligibility"], strings=tree_t)
    if lines:
        st.markdown("\n".join(lines))
    else:
        st.write(t["no_rules"])
    ex = [ln for e in d["exclusions"] for ln in tree_lines(e, exclusion=True, strings=tree_t)]
    if ex:
        st.markdown(f"**{t['exclusions']}**")
        st.markdown("\n".join(ex))
    if d["other_conditions"]:
        st.markdown(f"**{t['other_conditions']}**")
        for c in d["other_conditions"]:
            tag = f" {t['english_shown']}" if c.get("language_used") not in (None, lang) else ""
            st.markdown(f"- {c['text']}{tag}  \n  <small>{src(c['locator'])}</small>", unsafe_allow_html=True)

    st.markdown(f"### {t['documents']}")
    if d["documents"]:
        for kind, title in (("mandatory", t["doc_required"]), ("conditional", t["doc_conditional"]), ("optional", t["doc_optional"])):
            docs = [x for x in d["documents"] if x["requirement_type"] == kind]
            if docs:
                st.markdown(f"**{title}**")
                for x in docs:
                    note = f" — {x['purpose_note']}" if x.get("purpose_note") else ""
                    tag = f" {t['english_shown']}" if x.get("language_used") not in (None, lang) else ""
                    st.markdown(f"- {x['name']}{note}{tag}  \n  <small>{src(x['locator'])}</small>", unsafe_allow_html=True)
    else:
        st.write(t["no_documents"])

    st.markdown(f"### {t['how_to_apply']}")
    if d["steps"]:
        for stp in d["steps"]:
            tag = f" {t['english_shown']}" if stp.get("language_used") not in (None, lang) else ""
            st.markdown(f"{stp['number']}. {stp['instruction']}{tag}  \n   <small>{src(stp['locator'])}</small>", unsafe_allow_html=True)
    else:
        st.write(t["no_steps"])

    st.markdown(f"### {t['official_links']}")
    if not d["links"]:
        st.write(t["no_links"])
    for i, l in enumerate(d["links"]):
        if l["domain_class"] == "suspicious":
            st.error(t["link_suspicious"].format(url=l["url"]))
            continue
        origin = t["gov_domain"] if l["domain_class"] in ("gov_in", "nic_in") else t["other_domain"]
        st.link_button(f"{l.get('label') or l['link_type']} ↗", l["url"], key=f"link_{i}", help=t["opens_new_tab"])
        st.caption(f"{l['url']} · {origin} · {l['badge']}")
        if l.get("notes"):
            st.caption(l["notes"])

    srcs = collect_sources(d)
    if srcs:
        with st.expander(t["sources_n"].format(n=len(srcs))):
            for s in srcs:
                st.markdown(f"- [{s['title']}]({s['url']})")
    notice(d["notice"])


# ------------------------------------------------------------------------------------ eligibility
def _widget(f: dict, t: dict, yes: str, no: str) -> object:
    key, label = f"q_{f['key']}", f["label"]
    help_ = t["used_by"].format(names=", ".join(f["used_by"]))
    ty = f["type"]
    if ty == "bool":
        v = st.selectbox(label, [True, False], index=None, placeholder=t["prefer_not"], key=key, help=help_,
                         format_func=lambda x: yes if x else no)
        return v
    if ty == "enum":
        opts = list(f["value_labels"] or f["allowed_values"] or [])
        names = f["value_labels"] or {o: o.replace("_", " ").capitalize() for o in opts}
        return st.selectbox(label, opts, index=None, placeholder=t["prefer_not"], key=key, help=help_,
                            format_func=lambda o: names[o])
    if ty == "money_inr":
        return st.number_input(f"{label} (₹)", min_value=0, max_value=10**10, value=None, step=1000, key=key, help=help_)
    if ty == "int":
        return st.number_input(label, min_value=0, max_value=120 if f["key"] == "age_years" else 1000,
                               value=None, step=1, key=key, help=help_)
    return st.text_input(label, key=key, help=help_, max_chars=100)


def _clear_answers() -> None:
    for k in [k for k in st.session_state if k.startswith("q_")]:
        del st.session_state[k]
    st.session_state["evaluation"] = None


def _open_scheme(scheme_id: str) -> None:
    # a callback runs before the page radio is drawn again, so it may change the page safely
    st.session_state["page"] = PAGES[0]
    st.session_state["selected_scheme"] = scheme_id


def page_eligibility(lang: str, t: dict) -> None:
    st.header(t["check_title"])
    st.write(t["check_intro"])
    st.caption(t["check_privacy"])
    ui_all = api_client.call("GET", f"/api/v1/ui/{lang}")
    yes, no = ui_all["yes"], ui_all["no"]
    fields = [f for f in api_client.call("GET", "/api/v1/fields", params={"lang": lang})["fields"] if f["used_by"]]
    by_key = {f["key"]: f for f in fields}

    answers: dict[str, object] = {}
    with st.form("profile_form"):
        for f in (x for x in fields if not x["sensitive"]):
            answers[f["key"]] = _widget(f, t, yes, no)
        sensitive = [x for x in fields if x["sensitive"]]
        if sensitive:
            with st.expander(t["sensitive_title"]):
                st.caption(t["sensitive_note"])
                for f in sensitive:
                    answers[f["key"]] = _widget(f, t, yes, no)
        submitted = st.form_submit_button(t["check_button"], type="primary")
    st.button(t["clear_answers"], on_click=_clear_answers, key="clear")

    if submitted:
        profile = build_profile(answers)
        st.session_state["evaluation"] = {"profile": profile,
                                          "response": api_client.call("POST", "/api/v1/evaluate",
                                                                      json={"profile": profile, "lang": lang})}
    ev = st.session_state.get("evaluation")
    if ev:
        show_results(ev["response"], ev["profile"], by_key, t, yes, no)


def show_results(resp: dict, profile: dict, by_key: dict, t: dict, yes: str = "Yes", no: str = "No") -> None:
    st.divider()
    chat_results(resp, t)
    if not profile:
        st.warning(t["results_none_answered"])
    prio = question_priorities(resp["results"])
    if prio:
        st.markdown(f"### {t['results_helpful']}")
        for p in prio:
            tag = f" {t['results_sensitive']}" if p["sensitive"] else ""
            st.markdown(f"- **{p['label']}**{tag}: {t['results_affects'].format(n=len(p['schemes']))}")
    with st.expander(t["results_used"]):
        if profile:
            for k, v in profile.items():
                st.write(f"{by_key[k]['label']}: {format_value(by_key[k], v, yes=yes, no=no)}" if k in by_key else f"{k}: {v}")
        else:
            st.write(t["results_none"])


# ------------------------------------------------------------------------------------ about
def page_about(lang: str, t: dict) -> None:
    st.header(t["about_title"])
    st.write(t["about_intro"])
    for k in ("about_how", "about_meaning", "about_limits", "about_language"):
        st.markdown(t[k])
    h = api_client.call("GET", "/health")
    st.caption(t["service_status"].format(status=h["status"], n=h.get("schemes", t["unknown_word"])))


# ------------------------------------------------------------------------------------ chat
def _chat_turn(text: str, lang: str) -> None:
    chat = st.session_state["chat"]
    body = {"message": text, "language": lang}
    if chat["state"] is not None:
        body["state"] = chat["state"]
    reply = api_client.call("POST", "/api/v1/chat", json=body)
    chat["state"] = reply["state"]
    if text:
        chat["log"].append({"role": "user", "text": text})
    chat["log"].append({"role": "assistant", "text": "\n\n".join(reply["messages"]), "info": reply["info"]})
    chat["results"] = reply["results"]
    chat["done"] = reply["done"]


def _chat_reset() -> None:
    st.session_state["chat"] = {"state": None, "log": [], "results": None, "done": False}


def chat_results(res: dict, ui: dict) -> None:
    st.subheader(ui["results_title"])
    notice(res["notice"])
    for status, items in group_results(res["results"]):
        st.markdown(f"### {STATUS_ICON[status]} {items[0]['headline']} ({len(items)})")
        st.caption(ui["status_help"][status])
        for r in items:
            with st.expander(r["name"]):
                for x in r["reasons"]:
                    st.write(f"• {x['text']}")
                for x in r["warnings"]:
                    st.write(f"⚠️ {x['text']}")
                for rule in r["rules"]:
                    st.markdown(f"{rule_icon(rule)} {rule['explanation']}")
                    if rule.get("locator"):
                        st.caption(f"{ui['source']}: {rule['locator']}")
                if r["missing"]:
                    st.markdown(f"**{ui['would_change']}** " + ", ".join(m["label"] for m in r["missing"]))
                if r["confirm_officially"]:
                    st.markdown(f"**{ui['confirm_officially']}**")
                    for c in r["confirm_officially"]:
                        st.markdown(f"- {c['text']}" + (f" {ui['english_shown']}" if c.get("language_used") == "en" and r.get("language") != "en" else ""))
                rec = r.get("record") or {}
                st.caption(ui["record_line"].format(
                    verification=ui["verification"].get(rec.get("verification_status"), ""),
                    date=rec.get("last_verified") or ui["never"],
                    complete=ui["rules_complete"] if rec.get("rules_complete") else ui["rules_incomplete"]))


def page_chat(lang: str) -> None:
    ui = ui_text(lang)
    if "chat" not in st.session_state:
        _chat_reset()
    chat = st.session_state["chat"]
    st.header(ui["chat_title"])
    st.caption(ui["chat_privacy"])
    if st.button(ui["chat_start_over"], key="chat_reset"):
        _chat_reset()
        chat = st.session_state["chat"]
    if chat["state"] is None:
        _chat_turn("", lang)                       # opens with a greeting and the first question
    for i, m in enumerate(chat["log"]):
        with st.chat_message(m["role"]):
            st.markdown(m["text"])
            for j, item in enumerate(m.get("info") or []):
                for k, l in enumerate(item["links"]):
                    st.link_button(f"{l['label']} · {l['badge']}", l["url"], key=f"lnk_{i}_{j}_{k}")
    if chat["results"]:
        chat_results(chat["results"], ui)
    text = st.chat_input(ui["chat_input"])
    if text:
        _chat_turn(text.strip()[:10000], lang)
        st.rerun()


# ------------------------------------------------------------------------------------ main
def main() -> None:
    st.set_page_config(page_title="CitizenAssist AI", page_icon="🏛️", layout="centered")
    st.sidebar.title("CitizenAssist AI")
    lang = st.sidebar.radio("Language / भाषा", list(NATIVE), format_func=NATIVE.get, key="lang")
    try:
        t = ui_text(lang)
    except (ApiUnavailable, ApiProblem):
        t = None
    en = t or {}
    names = {CHAT: en.get("nav_chat", CHAT), PAGES[0]: en.get("nav_browse", PAGES[0]),
             PAGES[1]: en.get("nav_check", PAGES[1]), PAGES[2]: en.get("nav_about", PAGES[2])}
    page = st.sidebar.radio(en.get("nav_go", "Go to"), [CHAT] + PAGES, format_func=names.get, key="page")
    if page != PAGES[0]:
        st.session_state["selected_scheme"] = None
    if t is None:
        st.error("We could not reach the CitizenAssist service. Start it with "
                 "`python -m uvicorn app.main:create_app --factory` and reload this page.")
        return
    if page == CHAT:
        guarded(lambda: page_chat(lang), t)
    else:
        guarded(lambda: {PAGES[0]: page_browse, PAGES[1]: page_eligibility, PAGES[2]: page_about}[page](lang, t), t)


main()
