"""Command-line entry point: ``adaptiveroute --help``."""

from __future__ import annotations

import asyncio
from typing import Annotated

import typer

from adaptiveroute.config import get_settings

app = typer.Typer(help="AdaptiveRoute operations CLI.", no_args_is_help=True)


@app.command("create-api-key")
def create_api_key_cmd(
    name: Annotated[str, typer.Option(help="Human-readable owner/purpose")],
    role: Annotated[str, typer.Option(help="admin or user")] = "user",
    rate_limit: Annotated[int | None, typer.Option(help="Requests/minute override")] = None,
) -> None:
    """Create an API key and print it once (only its HMAC is stored)."""
    from adaptiveroute.api.security import create_api_key
    from adaptiveroute.db.session import create_engine, create_session_factory

    settings = get_settings()

    async def go() -> str:
        engine = create_engine(settings, pool=False)
        try:
            async with create_session_factory(engine)() as session:
                plaintext, _ = await create_api_key(
                    session,
                    name=name,
                    role=role,
                    pepper=settings.api_key_pepper.get_secret_value(),
                    rate_limit_per_minute=rate_limit,
                )
                await session.commit()
                return plaintext
        finally:
            await engine.dispose()

    key = asyncio.run(go())
    typer.echo(f"Created {role} key for {name!r}. Store it now; it will not be shown again:")
    typer.echo(key)


if __name__ == "__main__":
    app()
