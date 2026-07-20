from asyncio import Task, create_task, current_task, sleep
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock

from src.modules.core.tasks_manager import ActiveTask, list_tasks, next_task_id
from src.utils.i18n import t


class TaskIdTest(TestCase):
    def test_next_task_id_avoids_same_message_collisions(self) -> None:
        assert next_task_id({'1_2', '1_2_2'}, '1_2') == '1_2_3'


class TasksManagerTest(IsolatedAsyncioTestCase):
    async def test_list_tasks_uses_registered_metadata(self) -> None:
        worker = create_task(sleep(60, result=True))
        self.addCleanup(worker.cancel)
        started_at = datetime.now(UTC) - timedelta(seconds=5)
        reply = AsyncMock()
        event = SimpleNamespace(
            client=SimpleNamespace(
                active_tasks={
                    'self': ActiveTask(
                        cast(Task[bool], current_task()), '/tasks', 1, datetime.now(UTC)
                    ),
                    '1_2': ActiveTask(worker, 'm|<command>', 42, started_at),
                }
            ),
            reply=reply,
        )

        await list_tasks(cast(Any, event))

        message = cast(Any, reply.await_args).args[0]
        assert '<code>self</code>' not in message
        assert '<code>m|&lt;command&gt;</code>' in message
        assert f'<code>{started_at}</code>' in message

    async def test_list_tasks_ignores_itself_when_checking_for_tasks(self) -> None:
        reply = AsyncMock()
        event = SimpleNamespace(
            client=SimpleNamespace(
                active_tasks={
                    'self': ActiveTask(
                        cast(Task[bool], current_task()), '/tasks', 1, datetime.now(UTC)
                    )
                }
            ),
            reply=reply,
        )

        await list_tasks(cast(Any, event))

        reply.assert_awaited_once_with(t('no_active_tasks'))
