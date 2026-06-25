# cli package — entry point is cli.main:main (see pyproject.toml).
# Do NOT import main here: doing so would shadow the cli.main submodule
# and break ``import cli.main as m`` in tests.
