import asyncio
import logging

from .protocol.base import OK, ERR, EOF
from .protocol.flags import Capability
from .protocol.handshake import HandshakeV10, HandshakeResponse41, AuthSwitchRequest
from .protocol.query import ColumnDefinition, ColumnDefinitionList, ResultSet

logger = logging.getLogger(__name__)


class MysqlServer:
    def __init__(self, reader, writer):
        self.reader, self.writer = reader, writer

    def __iter__(self):
        exc = None

        info = await self.do_handshake()
        await self.connection_made(*info)

        try:
            await self.do_commands()
        except Exception as e:
            exc = e
            pass

        await self.connection_lost(exc)

    @classmethod
    def factory(cls, *args, **kw):
        async def cb(reader, writer):
            await cls(reader, writer, *args, **kw)

        return cb

    async def do_handshake(self):
        handshake = HandshakeV10()
        handshake.write(self.writer)
        await self.writer.drain()

        handshake_response = await HandshakeResponse41.read(self.reader.packet(), handshake.capability)

        self.status = handshake.status
        self.capability = handshake_response.capability_effective

        if (Capability.PLUGIN_AUTH in self.capability and
                handshake.auth_plugin != handshake_response.auth_plugin):
            AuthSwitchRequest().write(self.writer)
            await self.writer.drain()

            auth_response = await self.reader.packet().read()

        result = OK(self.capability, handshake.status)
        result.write(self.writer)
        await self.writer.drain()

        return handshake_response.user, handshake_response.schema

    async def do_commands(self):
        while True:
            result = None
            self.writer.reset()
            packet = self.reader.packet()

            try:
                cmd = (await packet.read(1))[0]

                if cmd == 1:
                    return
                elif cmd == 3:
                    result = await self.query(packet)
                else:
                    result = ERR(self.capability)

            except BrokenPipeError:
                return

            except Exception as e:
                logger.exception('Got exception during query')
                result = ERR(self.capability, error_msg='{}: {}'.format(e.__class__.__name__, e))

            finally:
                await packet.close()

            result.write(self.writer)
            await self.writer.drain()

    def connection_made(self, user, schema):
        yield

    def connection_lost(self, exp):
        yield

    def query(self, stream):
        raise NotImplementedError
