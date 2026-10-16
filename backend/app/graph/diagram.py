"""The agent graph as a Mermaid flowchart, drawn by LangGraph from the compiled graph, so it shows the real nodes
and edges: solid arrows always follow, dotted ones are chosen by a routing function. README.md shows it, and a test
keeps the two the same. Print it with: uv run python -m app.graph.diagram
"""
from app.graph.build import Deps, build_graph

AGENTS = ("coordinator", "extraction", "analytics")  # the three agents of the brief, highlighted


def mermaid() -> str:
    # Only the wiring is drawn, so the nodes get placeholders instead of models and agents.
    graph = build_graph(Deps(llm=lambda *args, **kwargs: None, decide=lambda question: None, workers=object()))
    text = graph.get_graph().draw_mermaid()
    text = text.replace("classDef default fill:#f2f0ff,line-height:1.2",  # the README diagrams' colours
                        "classDef default fill:#ffffff,stroke:#2d3142,line-height:1.2")
    text = text.replace("classDef last fill:#bfb6fc", "classDef last fill:#e4e4e9")
    return (text.rstrip() + "\n\tclassDef agent fill:#fbe9e3,stroke:#bf4520,stroke-width:1.5px\n"
            + f"\tclass {','.join(AGENTS)} agent\n")


if __name__ == "__main__":
    print(mermaid(), end="")
