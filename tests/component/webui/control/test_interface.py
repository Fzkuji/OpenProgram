"""Exact-window receipts and validation before interface effects."""
import asyncio
import json
import pytest

from openprogram.framework import interface


def test_interface_dispatch_and_forged_receipt():
    async def scenario():
        requests = []
        from openprogram.agent.authority import owner_authority, owner_principal_id
        class Socket:
            scope = {'state': {'authority': owner_authority(owner_principal_id())}}
            async def send_text(self, value):
                requests.append(json.loads(value))
        first, second = Socket(), Socket()
        await interface.register_window(first, {'window_id': 'one'})
        await interface.register_window(second, {'window_id': 'two'})
        try:
            with pytest.raises(ValueError, match='select_one'):
                await interface.invoke('tabs.list', [])
            task = asyncio.create_task(interface.invoke('tabs.list', [], 'one'))
            await asyncio.sleep(0)
            assert len(requests) == 1
            request_id = requests[0]['data']['request_id']
            await interface.result(second, {'request_id': request_id, 'result': {'ok': True, 'forged': True}})
            assert not task.done()
            await interface.result(first, {'request_id': request_id, 'result': {'ok': True, 'value': {'tabs': []}}})
            assert await task == {'ok': True, 'value': {'tabs': []}}
            task = asyncio.create_task(interface.invoke('tabs.list', [], 'one'))
            await asyncio.sleep(0)
            interface.release(first)
            assert (await task)['result_unconfirmed'] is True
            from jsonschema import ValidationError
            with pytest.raises(ValidationError):
                await interface.invoke('tabs.setSplitRatio', [5], 'two')
            assert len(requests) == 2
        finally:
            interface.release(first)
            interface.release(second)
    asyncio.run(scenario())
