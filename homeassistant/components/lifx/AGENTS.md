# LIFX custom override development

## Purpose

This directory is the development copy of Home Assistant's built-in `lifx`
integration. It is intentionally loaded as a custom integration on the Home
Assistant appliance so local changes override the packaged integration.

Keep this a drop-in replacement:

- The integration domain must remain `lifx` in `const.py` and `manifest.json`.
- Do not rename the component directory or create a second domain such as
  `lifx-ultimate`; the existing config entries use the `lifx` domain.
- `manifest.json` must retain a custom-integration `version` field. The name
  may identify this as the custom build, but the domain must stay `lifx`.

## Paths and machines

Development happens on the MacBook. The Home Assistant configuration share is
mounted locally, so edit the files in this directory directly:

```text
MacBook source:       /Volumes/config/workplace/ha-core/homeassistant/components/lifx
Home Assistant view:  /config/workplace/ha-core/homeassistant/components/lifx
Custom override link: /config/custom_components/lifx
Home Assistant host:  root@192.168.8.28
```

The appliance link must resolve to the source directory:

```sh
ssh root@192.168.8.28 'readlink /config/custom_components/lifx'
# Expected: /config/workplace/ha-core/homeassistant/components/lifx
```

Do not edit `/config/custom_components/lifx` as a separate copy; it is a
symlink to this directory.

## Relevant code and tests

- Integration setup, unload, and platforms: `__init__.py`
- Domain and shared constants: `const.py`
- User setup and discovery flows: `config_flow.py`, `discovery.py`
- LIFX communication and refresh coordination: `manager.py`, `coordinator.py`
- Light entities and service behavior: `light.py`
- Other entity platforms: `binary_sensor.py`, `button.py`, `select.py`,
  `sensor.py`
- Integration tests: `tests/components/lifx/`

Run Home Assistant development commands from the repository root:

```sh
cd /Volumes/config/workplace/ha-core
script/setup                         # first-time or refreshed local environment
uv run pytest tests/components/lifx  # focused LIFX test suite
uv run prek run --all-files          # before finishing a code session
```

Follow the parent repository's `AGENTS.md` for general Home Assistant coding
and test conventions.

## Deploy and verify on Home Assistant

Run Git staging, commits, and pushes from the Home Assistant host, not the mounted
macOS checkout:

```sh
ssh root@192.168.8.28 'cd /config/workplace/ha-core && git status --short'
```

Edits on the MacBook are immediately visible on the appliance through the
mount, but Home Assistant must restart to reload integration code:

```sh
ssh root@192.168.8.28 'ha core restart'
ssh root@192.168.8.28 'ha core info'
ssh root@192.168.8.28 'ha core logs --lines 300 2>&1 | grep -i -E "lifx|blocked|error"'
```

On startup, a warning that `lifx` is a custom integration is expected. A
message that it is blocked, a missing manifest version, or a reference to a
different domain is not expected and should be investigated before continuing.
