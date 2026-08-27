"""MC5000 driver: connection handling and wire format."""

from __future__ import annotations

import asyncio
import unittest

import _harness
from _harness import FakeBleakClient, load

mc5000 = load("mc5000_client")
Mc5000Client = mc5000.Mc5000Client


class FakeDevice:
    name = "TelinkSE"
    address = "AA:BB:CC:DD:EE:FF"


def _client() -> Mc5000Client:
    return Mc5000Client(FakeDevice())


async def _connect(client: Mc5000Client) -> FakeBleakClient:
    """Connect, answering each command the init sequence sends."""

    async def answer():
        # The init sequence and the handshake each wait on a notification;
        # feed one back for every packet written.
        for _ in range(200):
            await asyncio.sleep(0)
            if client._client is not None and client._client.notify_handler is not None:
                client._notify_queue.put_nowait(b"\x0f\x02\x06\x08")

    task = asyncio.create_task(answer())
    try:
        await client.connect()
    finally:
        task.cancel()

    return client._client


class ConnectionTest(unittest.IsolatedAsyncioTestCase):
    async def test_connect_goes_through_bleak_retry_connector(self) -> None:
        """A bare BleakClient.connect() fails hard on every transient error.

        establish_connection() retries them, reuses the cached GATT services
        and cooperates with Home Assistant's connection slot allocator, which
        is what stops a flaky link turning into a warning per poll.
        """
        _harness.establish_connection_calls.clear()
        client = _client()

        await _connect(client)

        self.assertEqual(len(_harness.establish_connection_calls), 1)
        call = _harness.establish_connection_calls[0]
        self.assertIs(call["device"], client._device)
        self.assertIsNotNone(call["disconnected_callback"])
        self.assertTrue(call["use_services_cache"])
        self.assertTrue(client.is_connected)

    async def test_disconnect_callback_marks_the_link_down(self) -> None:
        _harness.establish_connection_calls.clear()
        client = _client()
        await _connect(client)

        _harness.establish_connection_calls[0]["disconnected_callback"](client._client)

        self.assertFalse(client.is_connected)

    async def test_a_failed_init_leaves_no_half_open_client(self) -> None:
        """The charger accepts one connection; a leaked one blocks the retry."""
        _harness.establish_connection_calls.clear()
        client = _client()

        boom = RuntimeError("notify refused")

        async def failing_start_notify(_self, _char, _handler):
            raise boom

        original_init = FakeBleakClient.start_notify
        FakeBleakClient.start_notify = failing_start_notify
        try:
            with self.assertRaises(RuntimeError):
                await client.connect()
        finally:
            FakeBleakClient.start_notify = original_init

        self.assertIsNone(client._client)
        self.assertFalse(client.is_connected)

    async def test_set_ble_device_is_used_by_the_next_connect(self) -> None:
        _harness.establish_connection_calls.clear()
        client = _client()

        fresh = FakeDevice()
        client.set_ble_device(fresh)
        await _connect(client)

        self.assertIs(_harness.establish_connection_calls[0]["device"], fresh)


class PacketTest(unittest.TestCase):
    def test_packet_framing_and_checksum(self) -> None:
        packet = mc5000._build_packet(0x93, b"\x0f")

        self.assertEqual(packet[0], 0x0F)
        self.assertEqual(packet[1], len(packet) - 2)
        self.assertEqual(packet[-1], (0x93 + 0x0F) & 0xFF)

    def test_idle_slot_with_no_cell_reads_as_empty(self) -> None:
        payload = bytes(21)
        channel = Mc5000Client._parse_channel(0, payload)

        self.assertEqual(channel.status, "empty")
        self.assertEqual(channel.voltage, 0.0)

    def test_discharge_current_uses_its_own_multiplier(self) -> None:
        payload = bytearray(21)
        payload[2] = 0x07  # discharging
        payload[3] = 10  # current raw
        payload[4:6] = (3700).to_bytes(2, "big")

        channel = Mc5000Client._parse_channel(1, bytes(payload))

        self.assertEqual(channel.status, "discharging")
        self.assertEqual(channel.current, 0.1)
        self.assertEqual(channel.voltage, 3.7)

    def test_truncated_notification_is_not_thrown_away(self) -> None:
        payload = bytearray(17)
        payload[4:6] = (4100).to_bytes(2, "big")

        channel = Mc5000Client._parse_channel(2, bytes(payload))

        self.assertEqual(channel.voltage, 4.1)
        self.assertIsNone(channel.chemistry)


class StartAllTest(unittest.IsolatedAsyncioTestCase):
    async def test_start_all_arms_every_slot_in_one_command(self) -> None:
        """0x93 carries the complete set of running slots.

        Starting the slots one at a time would leave only the last one going.
        """
        client = _client()
        await _connect(client)
        client._client.writes.clear()

        async def answer():
            for _ in range(200):
                await asyncio.sleep(0)
                client._notify_queue.put_nowait(b"\x0f\x02\x94\x96")

        task = asyncio.create_task(answer())
        try:
            await client.start_all(chemistry="nimh")
        finally:
            task.cancel()

        commands = [write[2] for write in client._client.writes]
        self.assertEqual(commands.count(mc5000.CMD_CONFIG), 4)
        self.assertEqual(commands.count(mc5000.CMD_START_STOP), 1)

        start_packet = next(w for w in client._client.writes if w[2] == mc5000.CMD_START_STOP)
        self.assertEqual(start_packet[3], 0x0F)


if __name__ == "__main__":
    unittest.main()
