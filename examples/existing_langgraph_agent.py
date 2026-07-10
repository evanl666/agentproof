"""An existing LangGraph agent, as someone might have written it by hand.

Used by `agentproof import examples/existing_langgraph_agent.py` to show that
agents built elsewhere can be lifted into AgentProof, simulated, and repaired.
"""

from langgraph.graph import END, START, StateGraph


def planner(state):
    return state


def lookup_customer(state):
    return state


def process_refund(state):
    return state


def compose_response(state):
    return state


def send_email(state):
    return state


def build():
    graph = StateGraph(dict)
    graph.add_node("planner", planner)
    graph.add_node("lookup_customer", lookup_customer)
    graph.add_node("process_refund", process_refund)
    graph.add_node("compose_response", compose_response)
    graph.add_node("send_email", send_email)
    graph.add_edge(START, "planner")
    graph.add_edge("planner", "lookup_customer")
    graph.add_edge("lookup_customer", "planner")
    graph.add_edge("planner", "process_refund")
    graph.add_edge("process_refund", "planner")
    graph.add_edge("planner", "compose_response")
    graph.add_edge("compose_response", "send_email")
    graph.add_edge("send_email", END)
    return graph
