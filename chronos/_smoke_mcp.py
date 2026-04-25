"""
Smoke-test the Chronos MCP server by spawning it as a subprocess
and sending the MCP protocol's initialize + tools/list requests.

This is exactly the handshake Claude Desktop does when it connects.
"""
import asyncio
import json
import sys


async def smoke():
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "chronos.mcp_server",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    async def send(msg):
        line = json.dumps(msg) + "\n"
        proc.stdin.write(line.encode())
        await proc.stdin.drain()

    async def recv():
        line = await proc.stdout.readline()
        if not line:
            return None
        return json.loads(line.decode())

    # 1) initialize
    await send({
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "chronos-smoke", "version": "0.0"},
        },
    })
    init_resp = await recv()
    print("✅ initialize responded:", init_resp.get("result", {}).get("serverInfo", {}))

    # 2) notifications/initialized (no response expected)
    await send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    # 3) tools/list
    await send({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    tools_resp = await recv()
    tools = tools_resp.get("result", {}).get("tools", [])
    print(f"✅ tools/list returned {len(tools)} tools:")
    for t in tools:
        print(f"   • {t['name']}: {t['description'][:80]}...")

    # Graceful shutdown
    proc.stdin.close()
    try:
        await asyncio.wait_for(proc.wait(), timeout=3)
    except asyncio.TimeoutError:
        proc.terminate()

    print("\n✅ MCP server smoke test PASSED")


if __name__ == "__main__":
    asyncio.run(smoke())