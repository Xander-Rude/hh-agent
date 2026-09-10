from targeted_hunt_telegram_patch import _parts


def test_pipe_payload_parser_keeps_manual_intelligence_simple():
    assert _parts("Иви | Евгений Россинский | CTO") == [
        "Иви",
        "Евгений Россинский",
        "CTO",
    ]
