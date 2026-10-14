"""Graph routing, pauses, checkpoints and fallbacks, driven by scripted LLM replies (no network)."""
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from app.graph import prompts
from app.graph.build import Deps, build_graph
from app.graph.checkpoint import serializer, sqlite_checkpointer
from app.graph.nodes import reporting
from app.graph.nodes.intake import llm_decider
from app.graph.state import Claim, CoordinatorStep, Report, ReportTexts, Review, StandaloneQuestion, TriageDecision
from app.llm.fake import FakeLLMs

QUESTION = "Analyse employment trends in the technology sector from 2020-2024"
FACTS = [
    {"id": "F1", "label": "ICT employment, 2020", "value": 100_000, "unit": "persons"},
    {"id": "F2", "label": "ICT employment, 2024", "value": 125_000, "unit": "persons"},
    {"id": "F3", "label": "Change, 2020-2024", "value": 25.0, "unit": "%"},
]
SOURCES = [{"id": "S1", "title": "Employed residents by industry", "url": "https://data.gov.sg/datasets/x"}]
GOOD = Report(title="Tech employment grew", summary="ICT employment rose 25.0% between 2020 and 2024.",
              findings=[Claim(text="Employment rose from 100,000 to 125,000 persons.", citations=["F1", "F2", "S1"])])
BAD = Report(title="Tech employment grew", summary="ICT employment rose 31% between 2020 and 2024.",
             findings=[Claim(text="Employment reached 131,000 persons.", citations=["F2"])])
IN_SCOPE = TriageDecision(category="in_scope", reason="labour statistics question")
PASS = Review(verdict="pass", reason="answers the question")


class FakeWorkers:
    def __init__(self):
        self.calls = []

    def extract(self, instructions, question, previous=None):
        self.calls.append("extract")
        self.previous = previous
        return {"summary": "Loaded employment by industry", "datasets": ["employment"], "sources": SOURCES}

    def analyse(self, instructions, question, extraction, previous=None):
        self.calls.append("analyse")
        self.previous_analysis = previous
        return {"summary": "Computed ICT employment 2020-2024", "facts": FACTS, "charts": []}


def steps(*actions):
    return [CoordinatorStep(thought=f"next: {a}", action=a, instructions=f"please {a}") for a in actions]


def make(replies, **deps):
    llm, workers = FakeLLMs(replies), FakeWorkers()
    deps.setdefault("decide", llm_decider(llm))  # scripted triage; the real default loads the local model
    graph = build_graph(Deps(llm=llm, workers=workers, **deps), checkpointer=InMemorySaver(serde=serializer()))
    return graph, llm, workers


def ask(graph, text, thread="t1", **extra):
    config = {"configurable": {"thread_id": thread}}
    graph.invoke({"messages": [HumanMessage(text)], **extra}, config)
    return graph.get_state(config)


def resume(graph, value, thread="t1"):
    config = {"configurable": {"thread_id": thread}}
    graph.invoke(Command(resume=value), config)
    return graph.get_state(config)


def test_happy_path_extract_analyse_report_review():
    graph, llm, workers = make({"TriageDecision": [IN_SCOPE], "CoordinatorStep": steps("extract", "analyse", "report"),
                                "Report": [GOOD], "Review": [PASS]})
    state = ask(graph, QUESTION).values
    assert state["outcome"] == "answered" and state["grounding_issues"] == []
    assert "# Tech employment grew" in state["answer"] and "[F1, F2, S1]" in state["answer"]
    assert "[S1] Employed residents by industry" in state["answer"]
    assert state["report_html"].startswith("<!doctype html>") and 'id="F2"' in state["report_html"]
    assert isinstance(state["messages"][-1], AIMessage) and state["messages"][-1].content == state["answer"]
    # A first English message is used as-is: no rewriting call before triage.
    assert llm.keys() == ["TriageDecision", "CoordinatorStep", "CoordinatorStep", "CoordinatorStep", "Report", "Review"]
    assert workers.calls == ["extract", "analyse"]


def test_a_retried_extraction_receives_the_first_result():
    graph, _, workers = make({"TriageDecision": [IN_SCOPE],
                              "CoordinatorStep": steps("extract", "extract", "analyse", "report"),
                              "Report": [GOOD], "Review": [PASS]})
    assert ask(graph, QUESTION).values["outcome"] == "answered"
    assert workers.calls == ["extract", "extract", "analyse"] and workers.previous["sources"] == SOURCES


def test_off_topic_is_redirected_without_reaching_the_agents():
    graph, llm, workers = make({"TriageDecision": [TriageDecision(category="off_topic", reason="weather")]})
    state = ask(graph, "Will it rain tomorrow?").values
    assert state["answer"] == prompts.REDIRECT["en"] and state["outcome"] == "off_topic"
    assert llm.keys() == ["TriageDecision"] and workers.calls == []


def test_vague_question_pauses_for_a_clarifying_answer_then_resumes():
    graph, llm, _ = make({
        "TriageDecision": [TriageDecision(category="vague", reason="no topic"), IN_SCOPE],
        "text": ["Which area interests you: employment, housing or transport?"],
        "StandaloneQuestion": [StandaloneQuestion(question=QUESTION)],
        "CoordinatorStep": steps("extract", "analyse", "report"), "Report": [GOOD], "Review": [PASS]})
    paused = ask(graph, "Tell me something interesting")
    assert paused.next == ("ask_user",)
    assert paused.interrupts[0].value == {"kind": "clarification",
                                          "question": "Which area interests you: employment, housing or transport?"}
    state = resume(graph, "employment in tech, 2020 to 2024").values
    assert state["outcome"] == "answered" and state["clarifications"] == 1 and state["question"] == QUESTION
    # The rewrite step saw the clarifying question and the user's answer.
    rewrite_input = dict(llm.calls)["StandaloneQuestion"]
    assert [m.content for m in rewrite_input[-2:]] == ["Which area interests you: employment, housing or transport?",
                                                       "employment in tech, 2020 to 2024"]


def test_ungrounded_numbers_are_sent_back_then_fall_back_to_template():
    graph, llm, _ = make({"TriageDecision": [IN_SCOPE], "CoordinatorStep": steps("extract", "analyse", "report"),
                          "Report": [BAD, BAD, BAD]})
    state = ask(graph, QUESTION).values
    assert llm.keys().count("Report") == 3  # first draft + MAX_REVISIONS fixes
    revision_request = llm.calls[-1][1][-1].content
    assert "31 does not match any computed fact" in revision_request
    assert state["outcome"] == "fallback" and "Automated summary" in state["answer"]
    assert "ICT employment, 2024: 125000 persons [F2]" in state["answer"]


def test_caveats_are_checked_like_findings():
    uncited = GOOD.model_copy(update={"caveats": [Claim(text="Figures are June snapshots.", citations=[])]})
    wrong_number = GOOD.model_copy(update={"caveats": [Claim(text="About 7.5% of rows were removed.",
                                                             citations=["S1"])]})
    graph, llm, _ = make({"TriageDecision": [IN_SCOPE], "CoordinatorStep": steps("extract", "analyse", "report"),
                          "Report": [uncited, wrong_number, GOOD], "Review": [PASS]})
    state = ask(graph, QUESTION).values
    requests = [m[-1].content for key, m in llm.calls if key == "Report"]
    assert "caveat 1: has no citation" in requests[1]
    assert "caveat 1: 7.5 does not match any computed fact or a source this caveat cites" in requests[2]
    assert state["outcome"] == "answered" and state["revisions"] == 2


def test_a_caveat_may_quote_the_quality_report_of_the_dataset_it_cites():
    quality = "5,546 values (one per measure per row) from 12,012 source rows"
    sources = [{"id": "S1", "kind": "dataset", "title": "Medians", "quality": quality}, {"id": "S2", "kind": "dataset"}]
    report = Report(title="t", summary="s", findings=[Claim(text="Prices rose.", citations=["S1"])],
                    caveats=[Claim(text="5,546 values came from 12,012 source rows.", citations=["S1"])])
    state = {"question": "q", "report": report, "findings": {"extraction": {"sources": sources}}}
    assert reporting.validate(state)["grounding_issues"] == []
    wrong_source = report.model_copy(update={"caveats": [Claim(text="5,546 values remained.", citations=["S2"])]})
    assert reporting.validate({**state, "report": wrong_source})["grounding_issues"] == [
        "caveat 1: 5,546 does not match any computed fact or a source this caveat cites"]


def test_reports_saved_with_plain_caveats_still_load():
    old = Report.model_validate({"title": "t", "summary": "s", "findings": [], "caveats": ["Residents only."]})
    assert old.caveats == [Claim(text="Residents only.", citations=[])]


def test_fixed_report_passes_after_one_revision():
    graph, llm, _ = make({"TriageDecision": [IN_SCOPE], "CoordinatorStep": steps("extract", "analyse", "report"),
                          "Report": [BAD, GOOD], "Review": [PASS]})
    state = ask(graph, QUESTION).values
    assert state["outcome"] == "answered" and state["revisions"] == 1


def test_reviewer_sends_wrong_analysis_back_to_the_coordinator():
    graph, llm, workers = make({
        "TriageDecision": [IN_SCOPE], "CoordinatorStep": steps("extract", "analyse", "report", "analyse", "report"),
        "Report": [GOOD, GOOD], "Review": [Review(verdict="wrong_analysis", reason="use residents only"), PASS]})
    state = ask(graph, QUESTION).values
    assert state["outcome"] == "answered" and workers.calls == ["extract", "analyse", "analyse"]
    assert workers.previous_analysis["facts"] == FACTS  # the second analysis builds on the first
    coordinator_prompt = [m for key, m in llm.calls if key == "CoordinatorStep"][3][0].content
    assert "Reviewer sent the analysis back: use residents only" in coordinator_prompt
    # What is loaded and computed comes from the state, not only from the agents' own summaries.
    assert "Datasets loaded and ready to analyse: S1 Employed residents by industry" in coordinator_prompt
    assert "Facts computed so far: 3; charts: 0" in coordinator_prompt


def test_the_reviewer_sends_the_analysis_back_only_once():
    wrong = Review(verdict="wrong_analysis", reason="compare all flat types")
    graph, _, workers = make({"TriageDecision": [IN_SCOPE],
                              "CoordinatorStep": steps("extract", "analyse", "report", "analyse", "report"),
                              "Report": [GOOD, GOOD], "Review": [wrong, wrong]})
    state = ask(graph, QUESTION).values
    assert workers.calls == ["extract", "analyse", "analyse"]  # one rework, not a loop until the step limit
    assert state["outcome"] == "partial" and "Reviewer note: compare all flat types" in state["answer"]


def test_a_report_the_reviewer_still_rejects_is_partial():
    poor = Review(verdict="poor_report", reason="does not compare with overall employment")
    graph, _, _ = make({"TriageDecision": [IN_SCOPE], "CoordinatorStep": steps("extract", "analyse", "report"),
                        "Report": [GOOD, GOOD, GOOD], "Review": [poor, poor, poor]})
    state = ask(graph, QUESTION).values
    assert state["outcome"] == "partial" and state["revisions"] == 2
    assert "Reviewer note: does not compare with overall employment" in state["answer"]


def test_only_cited_passages_are_listed_as_sources():
    sources = [{"id": "S1", "kind": "dataset"}, {"id": "D1", "kind": "document"}, {"id": "D2", "kind": "document"},
               {"id": "W1", "kind": "web"}]
    report = Report(title="t", summary="s", findings=[Claim(text="x", citations=["F1", "D2"])])
    assert [s["id"] for s in reporting.shown_sources(sources, report)] == ["S1", "D2"]
    assert [s["id"] for s in reporting.shown_sources(sources, None)] == ["S1"]


def test_llm_failure_routes_to_deterministic_fallback():
    graph, _, _ = make({"TriageDecision": [IN_SCOPE],
                        "CoordinatorStep": [*steps("extract", "analyse"), RuntimeError("both providers down")]})
    state = ask(graph, QUESTION).values
    assert state["outcome"] == "fallback" and state["failed_node"] == "coordinator"
    assert "coordinator: RuntimeError: both providers down" in state["answer"]
    assert "[F3]" in state["answer"]


def zh_texts(finding: str) -> ReportTexts:
    return ReportTexts(title="科技就业增长", summary="2020年至2024年，资讯通信业就业增长25.0%。", findings=[finding],
                       caveats=[], chart_titles=[])


def test_chinese_question_is_answered_in_chinese_with_numbers_checked():
    graph, llm, _ = make({"StandaloneQuestion": [StandaloneQuestion(question=QUESTION)], "TriageDecision": [IN_SCOPE],
                          "CoordinatorStep": steps("extract", "analyse", "report"), "Report": [GOOD],
                          "Review": [PASS], "ReportTexts": [zh_texts("就业人数从100,000增至125,000。")]})
    state = ask(graph, "分析2020至2024年科技行业的就业趋势").values
    assert state["language"] == "zh" and state["question"] == QUESTION
    assert state["answer"].startswith("# 科技就业增长")
    assert "就业人数从100,000增至125,000。 [F1, F2, S1]" in state["answer"]
    assert state["report"].title == "Tech employment grew"  # the checked English report is kept for the record
    assert '<html lang="zh">' in state["report_html"] and "主要发现" in state["report_html"]
    assert llm.keys()[0] == "StandaloneQuestion"  # non-English input is rewritten into English first


def test_translation_that_changes_a_number_falls_back_to_english():
    graph, _, _ = make({"StandaloneQuestion": [StandaloneQuestion(question=QUESTION)], "TriageDecision": [IN_SCOPE],
                        "CoordinatorStep": steps("extract", "analyse", "report"), "Report": [GOOD],
                        "Review": [PASS], "ReportTexts": [zh_texts("就业人数从100,000增至130,000。")]})
    state = ask(graph, "分析2020至2024年科技行业的就业趋势").values
    assert state["answer"].startswith("# Tech employment grew") and '<html lang="zh">' in state["report_html"]


def test_second_turn_keeps_history_and_resets_working_state():
    graph, llm, _ = make({
        "TriageDecision": [IN_SCOPE, IN_SCOPE], "StandaloneQuestion": [StandaloneQuestion(question="Same for 2019")],
        "CoordinatorStep": steps("extract", "analyse", "report", "decline"), "Report": [GOOD], "Review": [PASS]})
    ask(graph, QUESTION)
    state = ask(graph, "what about 2019?").values
    assert len(state["messages"]) == 4 and state["outcome"] == "declined"
    assert [s.action for s in state["coordinator_steps"]] == ["decline"]  # turn 1's steps were cleared
    assert "StandaloneQuestion" in llm.keys()  # follow-ups are rewritten with the conversation history


def test_plan_approval_pause_when_enabled():
    graph, _, workers = make({"TriageDecision": [IN_SCOPE], "CoordinatorStep": steps("extract", "analyse", "report"),
                              "Report": [GOOD], "Review": [PASS]})
    paused = ask(graph, QUESTION, require_plan_approval=True)
    assert paused.interrupts[0].value["kind"] == "plan_approval" and workers.calls == []
    state = resume(graph, {"approved": True}).values
    assert state["outcome"] == "answered" and state["plan_approved"]


def test_rejecting_the_plan_without_feedback_cancels():
    graph, _, workers = make({"TriageDecision": [IN_SCOPE], "CoordinatorStep": steps("extract")})
    ask(graph, QUESTION, require_plan_approval=True)
    state = resume(graph, {"approved": False}).values
    assert state["outcome"] == "cancelled" and workers.calls == []


def test_coordinator_can_decline_unanswerable_questions():
    graph, _, workers = make({"TriageDecision": [IN_SCOPE], "CoordinatorStep": [CoordinatorStep(
        thought="This asks for a forecast", action="decline", instructions="Official data does not forecast 2030.")]})
    state = ask(graph, "What will HDB prices be in 2030?").values
    assert state["outcome"] == "declined" and "does not forecast 2030" in state["answer"] and workers.calls == []


def test_ledger_attributes_each_llm_call_to_its_agent():
    """Real provider wiring (chat + fallback) with scripted models: the ledger passed in the run config sees
    every call made inside the nodes, labelled with the node that made it."""
    from app.llm.fake import ScriptedChatModel, structured
    from app.llm.ledger import Ledger
    from app.llm.providers import chat

    script = {"TriageDecision": [structured(IN_SCOPE)], "CoordinatorStep": [structured(s) for s in steps(
        "extract", "analyse", "report")], "Report": [structured(GOOD)], "Review": [structured(PASS)]}

    def factory(tier, *, tools=None, schema=None):
        primary = ScriptedChatModel(script=[RuntimeError("primary down")], model_name="gpt-6.1-sol", provider="openai")
        fallback = ScriptedChatModel(script=[script[schema.__name__].pop(0)], model_name="claude-sonnet-5-5",
                                     provider="anthropic")
        return chat(tier, schema=schema, models=(primary, fallback))

    ledger = Ledger()
    graph = build_graph(Deps(llm=factory, workers=FakeWorkers(), decide=llm_decider(factory)),
                        checkpointer=InMemorySaver())
    graph.invoke({"messages": [HumanMessage(QUESTION)]}, {"configurable": {"thread_id": "x"}, "callbacks": [ledger]})
    ok = [(c.agent, c.provider) for c in ledger.calls if c.ok]
    assert ok == [("triage", "anthropic"), *[("coordinator", "anthropic")] * 3, ("report_writer", "anthropic"),
                  ("reviewer", "anthropic")]
    assert ledger.totals()["failed_llm_calls"] == 6  # every primary attempt failed and was recorded


def test_paused_conversation_survives_a_restart(tmp_path):
    """Stateless requests: a new process (new graph, new connection) resumes a paused thread from SQLite."""
    replies = {"TriageDecision": [TriageDecision(category="vague", reason="no topic"), IN_SCOPE],
               "text": ["Which area: employment or housing?"],
               "StandaloneQuestion": [StandaloneQuestion(question=QUESTION)],
               "CoordinatorStep": steps("extract", "analyse", "report"), "Report": [GOOD], "Review": [PASS]}
    llm = FakeLLMs(replies)
    first = build_graph(Deps(llm=llm, workers=FakeWorkers(), decide=llm_decider(llm)),
                        checkpointer=sqlite_checkpointer(tmp_path / "c.db"))
    assert ask(first, "Tell me something").next == ("ask_user",)
    second = build_graph(Deps(llm=llm, workers=FakeWorkers(), decide=llm_decider(llm)),
                         checkpointer=sqlite_checkpointer(tmp_path / "c.db"))
    state = resume(second, "employment").values
    assert state["outcome"] == "answered" and state["coordinator_steps"][0].action == "extract"
