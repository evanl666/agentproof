from agentproof.graph import NodeType


def test_graph_has_agent_loop_shape(naive_graph):
    ids = {n.id for n in naive_graph.nodes}
    assert {"input", "planner", "responder", "send_email", "output"} <= ids


def test_tools_inferred_from_capabilities(naive_graph):
    refund = naive_graph.node("process_refund")
    assert refund.config.get("spend") is True
    lookup = naive_graph.node("lookup_customer")
    assert lookup.config.get("returns_pii") is True
    email = naive_graph.node("send_email")
    assert email.config.get("external") is True


def test_naive_graph_has_no_enforcement_structure(naive_graph):
    assert not naive_graph.nodes_of_type(NodeType.CONDITION)
    assert not naive_graph.nodes_of_type(NodeType.APPROVAL)
    assert not naive_graph.nodes_of_type(NodeType.GUARD)


def test_tools_loop_back_to_planner(naive_graph):
    assert any(e.source == "lookup_customer" and e.target == "planner" for e in naive_graph.edges)


def test_graph_roundtrips_through_dict(naive_graph):
    from agentproof.graph import AgentGraph

    clone = AgentGraph.from_dict(naive_graph.to_dict())
    assert {n.id for n in clone.nodes} == {n.id for n in naive_graph.nodes}
    assert len(clone.edges) == len(naive_graph.edges)
