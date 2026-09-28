import os
import uuid
from contextlib import contextmanager

import psycopg
import pytest
from analysis.graph import build_graph
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.types import Command
from psycopg.rows import dict_row


@pytest.mark.skipif(
    os.environ.get("RUN_POSTGRES_TESTS") != "1", reason="requires dedicated PostgreSQL"
)
@pytest.mark.django_db(transaction=True)
def test_checkpoint_survives_graph_and_connection_restart(settings):
    from django.db import connection

    # Use the Django-created test database, never the developer's application database.
    db = connection.settings_dict

    @contextmanager
    def new_saver():
        with psycopg.connect(
            host=db["HOST"],
            port=db["PORT"],
            dbname=db["NAME"],
            user=db["USER"],
            password=db["PASSWORD"],
            autocommit=True,
            row_factory=dict_row,
            prepare_threshold=0,
        ) as conn:
            yield PostgresSaver(conn)

    thread_id = str(uuid.uuid4())

    def graph_with(saver):
        return build_graph(
            saver,
            lambda *args: None,
            lambda *args: ["e1"],
            lambda ids: {"e1": "供水数字化"},
            lambda *args: {
                "claims": [{"text": "供水数字化", "evidence_id": "e1", "quote": "供水数字化"}]
            },
        )

    with new_saver() as saver:
        saver.setup()
        result = graph_with(saver).invoke(
            {"actor_id": 1, "policy_id": "p1", "input_version": 1, "question": "主题？"},
            {"configurable": {"thread_id": thread_id}},
        )
        assert result["status"] == "needs_review"
    with new_saver() as saver:
        result = graph_with(saver).invoke(
            Command(resume=True), {"configurable": {"thread_id": thread_id}}
        )
        assert result["status"] == "reviewed"
