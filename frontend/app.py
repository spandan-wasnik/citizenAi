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
from frontend.ui_helpers import (LEVEL_TEXT, SCHEME_STATUS_TEXT, STATUS_HELP, STATUS_ICON,  # noqa: E402
                                 VERIFICATION_TEXT, build_profile, collect_sources, format_value,
                                 group_results, question_priorities, rule_icon, tree_lines)

PAGES = ["Browse schemes", "Check my eligibility", "About"]
CHAT = "Chat"
NATIVE = {"en": "English", "hi": "हिन्दी", "mr": "मराठी"}
LINK_TYPE_TEXT = {"official_portal": "Official portal", "application": "Apply", "information": "More information",
                  "guidelines": "Guidelines", "helpline": "Helpline"}


# ------------------------------------------------------------------------------------ shared
def guarded(fn):
    """Run a page; turn API trouble into a plain-language message instead of a traceback."""
    try:
        fn()
    except ApiUnavailable:
        st.error("We could not reach the CitizenAssist service. Start it with "
                 "`python -m uvicorn app.main:create_app --factory` and reload this page.")
    except ApiProblem as e:
        if e.code == "database_not_ready":
            st.error("The scheme database has not been created yet. Run `python -m scripts.init_db` "
                     "and `python -m scripts.seed_database`, then reload.")
        else:
            st.error(f"The service reported a problem ({e.code}): {e.message}")


def notice(text: str) -> None:
    st.info(text, icon="ℹ️")


# ------------------------------------------------------------------------------------ browse
def page_browse() -> None:
    st.header("Browse schemes")
    if st.session_state.get("selected_scheme"):
        return show_detail(st.session_state["selected_scheme"])

    fields = {f["key"]: f for f in api_client.call("GET", "/api/v1/fields")["fields"]}
    states = fields["state_code"]["value_labels"]
    c1, c2, c3 = st.columns([3, 1, 2])
    q = c1.text_input("Search by name or topic", key="browse_q", max_chars=100)
    level = c2.selectbox("Level", ["All", "central", "state"], format_func=lambda x: LEVEL_TEXT.get(x, x), key="browse_level")
    state = c3.selectbox("State", [None] + list(states), format_func=lambda c: "All states" if c is None else states[c],
                         key="browse_state", help="Central schemes are always included.")
    params = {k: v for k, v in {"q": q.strip() or None, "level": None if level == "All" else level, "state": state}.items() if v}
    data = api_client.call("GET", "/api/v1/schemes", params=params)
    st.caption(f"{data['count']} scheme(s) found")
    if not data["count"]:
        st.warning("No scheme in our records matches. We only list schemes whose details we have checked against official sources.")
    for s in data["schemes"]:
        with st.container(border=True):
            st.subheader(s["name"])
            st.caption(f"{LEVEL_TEXT.get(s['level'], s['level'])} scheme · {SCHEME_STATUS_TEXT.get(s['status'], s['status'])} · "
                       f"{VERIFICATION_TEXT.get(s['verification_status'], s['verification_status'])}")
            if st.button("View details", key=f"view_{s['scheme_id']}"):
                st.session_state["selected_scheme"] = s["scheme_id"]
                st.rerun()


def show_detail(scheme_id: str) -> None:
    if st.button("← Back to all schemes", key="back"):
        st.session_state["selected_scheme"] = None
        st.rerun()
    try:
        d = api_client.call("GET", f"/api/v1/schemes/{scheme_id}")
    except ApiProblem as e:
        if e.code == "scheme_not_found":
            st.session_state["selected_scheme"] = None
            st.warning("That scheme is not in our records.")
            return
        raise
    tr, rec = d["translation"], d["record"]
    st.subheader(tr["name"] if tr else d["scheme_id"])
    badges = [VERIFICATION_TEXT.get(rec["verification_status"], rec["verification_status"]),
              SCHEME_STATUS_TEXT.get(rec["status"], rec["status"])]
    if rec["is_stale"]:
        badges.append("Overdue for re-check")
    st.caption(" · ".join(badges))
    if rec["last_verified"]:
        st.caption(f"Last checked against the official source: {rec['last_verified']}")
    if not rec["rules_complete"]:
        st.warning("Our list of rules for this scheme may be incomplete. Always read the official conditions before applying.")
    if rec["is_stale"]:
        st.warning("This record is overdue for a re-check, so details may have changed.")
    if tr:
        for title, key in (("What it is", "short_description"), ("Benefits", "benefits"), ("Who it is for", "target_beneficiaries")):
            if tr.get(key):
                st.markdown(f"**{title}**")
                st.write(tr[key])
    st.caption(" · ".join(x for x in (d.get("ministry"), d.get("department")) if x))

    st.markdown("### Eligibility rules")
    lines = tree_lines(d["eligibility"])
    if lines:
        st.markdown("\n".join(lines))
    else:
        st.write("We have no verified rules recorded that can be checked from your answers.")
    ex = [ln for e in d["exclusions"] for ln in tree_lines(e, exclusion=True)]
    if ex:
        st.markdown("**Exclusions**")
        st.markdown("\n".join(ex))
    if d["other_conditions"]:
        st.markdown("**Other official conditions (confirm these officially)**")
        for c in d["other_conditions"]:
            st.markdown(f"- {c['text']}  \n  <small>source: {c['locator']}</small>", unsafe_allow_html=True)

    st.markdown("### Documents")
    if d["documents"]:
        for kind, title in (("mandatory", "Required"), ("conditional", "Needed only in some cases (see the official source)"), ("optional", "Optional")):
            docs = [x for x in d["documents"] if x["requirement_type"] == kind]
            if docs:
                st.markdown(f"**{title}**")
                for x in docs:
                    note = f" — {x['purpose_note']}" if x.get("purpose_note") else ""
                    st.markdown(f"- {x['name']}{note}  \n  <small>source: {x['locator']}</small>", unsafe_allow_html=True)
    else:
        st.write("We have no verified document list for this scheme.")

    st.markdown("### How to apply")
    if d["steps"]:
        for stp in d["steps"]:
            st.markdown(f"{stp['number']}. {stp['instruction']}  \n   <small>source: {stp['locator']}</small>", unsafe_allow_html=True)
    else:
        st.write("We have no verified application steps for this scheme. Please use the official links below, if any.")

    st.markdown("### Official links")
    if not d["links"]:
        st.write("We have no verified official link for this scheme yet. We do not guess web addresses.")
    for i, l in enumerate(d["links"]):
        label = LINK_TYPE_TEXT.get(l["link_type"], l["link_type"])
        if l["domain_class"] == "suspicious":
            st.error(f"A link for this scheme failed our safety checks, so we are not showing it as a button: {l['url']}")
            continue
        origin = ("Government domain (.gov.in / .nic.in)" if l["domain_class"] in ("gov_in", "nic_in")
                  else "Domain not on our government list: check it is official")
        st.link_button(f"{label} ↗", l["url"], key=f"link_{i}", help="Opens in a new tab")
        st.caption(f"{l['url']} · {origin} · {l['badge']}")
        if l.get("notes"):
            st.caption(l["notes"])

    srcs = collect_sources(d)
    if srcs:
        with st.expander(f"Sources ({len(srcs)})"):
            for s in srcs:
                st.markdown(f"- [{s['title']}]({s['url']})")
    notice(d["notice"])


# ------------------------------------------------------------------------------------ eligibility
def _widget(f: dict) -> object:
    key, label = f"q_{f['key']}", f["label"]
    help_ = "Used by: " + ", ".join(f["used_by"])
    t = f["type"]
    if t == "bool":
        v = st.selectbox(label, ["Yes", "No"], index=None, placeholder="Prefer not to say", key=key, help=help_)
        return None if v is None else v == "Yes"
    if t in ("enum",):
        opts = list(f["value_labels"] or f["allowed_values"] or [])
        names = f["value_labels"] or {o: o.replace("_", " ").capitalize() for o in opts}
        return st.selectbox(label, opts, index=None, placeholder="Prefer not to say", key=key, help=help_,
                            format_func=lambda o: names[o])
    if t == "money_inr":
        return st.number_input(f"{label} (₹)", min_value=0, max_value=10**10, value=None, step=1000, key=key, help=help_)
    if t == "int":
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


def page_eligibility() -> None:
    st.header("Check my eligibility")
    st.write("Answer what you are comfortable sharing. Leave a question blank if you prefer not to answer: "
             "we never treat a blank as 'no' or zero, and we only ask what the recorded rules use.")
    st.caption("Your answers stay in this browser session, are sent only to our own service to run the check, "
               "and are not saved.")
    fields = [f for f in api_client.call("GET", "/api/v1/fields")["fields"] if f["used_by"]]
    by_key = {f["key"]: f for f in fields}

    answers: dict[str, object] = {}
    with st.form("profile_form"):
        for f in (x for x in fields if not x["sensitive"]):
            answers[f["key"]] = _widget(f)
        sensitive = [x for x in fields if x["sensitive"]]
        if sensitive:
            with st.expander("Sensitive questions (optional)"):
                st.caption("Only answer these if you want a more complete result. They are used for the check only.")
                for f in sensitive:
                    answers[f["key"]] = _widget(f)
        submitted = st.form_submit_button("Check schemes", type="primary")
    st.button("Clear my answers", on_click=_clear_answers, key="clear")

    if submitted:
        profile = build_profile(answers)
        st.session_state["evaluation"] = {"profile": profile,
                                          "response": api_client.call("POST", "/api/v1/evaluate",
                                                                      json={"profile": profile, "lang": "en"})}
    ev = st.session_state.get("evaluation")
    if ev:
        show_results(ev["response"], ev["profile"], by_key)


def show_results(resp: dict, profile: dict, by_key: dict) -> None:
    st.divider()
    st.subheader("Your results")
    notice(resp["notice"])
    if not profile:
        st.warning("You did not answer any questions, so most results will say more information is needed.")
    for status, items in group_results(resp["results"]):
        st.markdown(f"### {STATUS_ICON[status]} {items[0]['headline']} ({len(items)})")
        st.caption(STATUS_HELP[status])
        for r in items:
            with st.expander(r["name"]):
                for x in r["reasons"]:
                    st.write(f"• {x['text']}")
                for x in r["warnings"]:
                    st.write(f"⚠️ {x['text']}")
                for rule in r["rules"]:
                    st.markdown(f"{rule_icon(rule)} {rule['explanation']}")
                    if rule.get("locator"):
                        st.caption(f"Source: {rule['locator']}")
                if r["missing"]:
                    st.markdown("**Answers that would change the outcome:** " + ", ".join(m["label"] for m in r["missing"]))
                if r["confirm_officially"]:
                    st.markdown("**Confirm these officially (we cannot check them):**")
                    for c in r["confirm_officially"]:
                        st.markdown(f"- {c['text']}")
                rec = r.get("record") or {}
                st.caption(f"Record: {VERIFICATION_TEXT.get(rec.get('verification_status'), '')} · "
                           f"last checked {rec.get('last_verified') or 'never'} · rules {'complete' if rec.get('rules_complete') else 'may be incomplete'}")
                st.button("Open scheme details", key=f"open_{r['scheme_id']}", on_click=_open_scheme, args=(r["scheme_id"],))

    prio = question_priorities(resp["results"])
    if prio:
        st.markdown("### Answers that would help most")
        for p in prio:
            tag = " (sensitive, optional)" if p["sensitive"] else ""
            st.markdown(f"- **{p['label']}**{tag}: affects {len(p['schemes'])} scheme(s)")
    with st.expander("Answers used for this check"):
        if profile:
            for k, v in profile.items():
                st.write(f"{by_key[k]['label']}: {format_value(by_key[k], v)}" if k in by_key else f"{k}: {v}")
        else:
            st.write("None.")


# ------------------------------------------------------------------------------------ about
def page_about() -> None:
    st.header("About CitizenAssist AI")
    st.write("CitizenAssist helps you find out which government schemes you may qualify for, which documents you "
             "may need, and where the official application is. It is not an official government service.")
    st.markdown("""
**How results are decided.** Fixed rules copied from official documents are applied to your answers. No AI guesses
are involved in eligibility. Each rule shows where it came from.

**What the results mean**
- 🟢 *Likely eligible*: every rule we could check is met and our record is verified and complete.
- 🟡 *More information needed*: an answer you left blank could change the outcome.
- 🟠 *Needs official verification*: our record is not complete or current enough for a firm answer.
- 🔴 *Likely not eligible*: a verified rule is not met.

**Honest limits.** We list only a few schemes so far, each checked against official text we were given.
Rules and deadlines change, so a result is never an approval. Links open the official site in a new tab.

**Language.** Only English is available at the moment. Hindi and Marathi are planned and will not be
simulated before they are real.
""")
    h = api_client.call("GET", "/health")
    st.caption(f"Service status: {h['status']} · schemes recorded: {h.get('schemes', 'unknown')}")


# ------------------------------------------------------------------------------------ chat
def ui_text(lang: str) -> dict:
    """Interface strings for the chosen language, fetched from the API (one wording source)."""
    cache = st.session_state.setdefault("ui_cache", {})
    if lang not in cache:
        cache[lang] = api_client.call("GET", f"/api/v1/ui/{lang}")["ui"]
    return cache[lang]


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
    lang = st.sidebar.radio("Language / भाषा / भाषा", list(NATIVE), format_func=NATIVE.get, key="lang")
    try:
        ui = ui_text(lang)
    except (ApiUnavailable, ApiProblem):
        ui = None
    names = {CHAT: (ui or {}).get("nav_chat", CHAT), PAGES[0]: (ui or {}).get("nav_browse", PAGES[0]),
             PAGES[1]: (ui or {}).get("nav_check", PAGES[1]), PAGES[2]: (ui or {}).get("nav_about", PAGES[2])}
    page = st.sidebar.radio("Go to", [CHAT] + PAGES, format_func=names.get, key="page")
    if page != PAGES[0]:
        st.session_state["selected_scheme"] = None
    if page == CHAT:
        guarded(lambda: page_chat(lang))
        return
    if ui and lang != "en":
        st.caption(ui["english_only_page"])
    guarded({PAGES[0]: page_browse, PAGES[1]: page_eligibility, PAGES[2]: page_about}[page])


main()
