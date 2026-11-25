"""What the author gave or approved survives a rewrite of the world model."""
from auto_research.tasks.worldmap.workflow import keep_notes


def test_notes_and_approved_entries_are_carried_over():
    previous = {
        "entities": [
            {"id": "E1", "name": "Probe", "type": "method", "source": "notes:ideas.md"},
            {"id": "E2", "name": "Dataset X", "type": "dataset", "approved": True},
            {"id": "E3", "name": "Old guess", "type": "method"},
        ],
        "relations": [
            {"from": "E1", "to": "E2", "type": "evaluated_on", "source": "notes:ideas.md"},
            {"from": "E1", "to": "E3", "type": "extends"},
        ],
    }
    world = {"entities": [{"id": "E9", "name": "Baseline", "type": "method"}], "relations": []}
    kept = keep_notes(world, previous)
    assert [e["id"] for e in kept["entities"]] == ["E9", "E1", "E2"]
    assert kept["relations"] == [previous["relations"][0]]


def test_an_entry_the_new_model_kept_is_not_duplicated():
    previous = {"entities": [{"id": "E1", "name": "Probe", "type": "method", "approved": True}], "relations": []}
    world = {"entities": [{"id": "E5", "name": "probe", "type": "method"}], "relations": []}
    assert len(keep_notes(world, previous)["entities"]) == 1
    assert keep_notes(world, {}) is world
