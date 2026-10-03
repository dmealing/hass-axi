# ha-axi has been renamed to hass-axi

This is the last release of `ha-axi`. The Home Assistant AXI CLI it used to install is now published
as [`hass-axi`](https://pypi.org/project/hass-axi/), because an unrelated Home Assistant CLI is also
published as `ha-axi` and the two installed the same binary.

This package contains no code of its own. It depends on `hass-axi`, so upgrading it installs the
renamed tool, and its `ha-axi` command forwards to `hass-axi` after a one-line notice on stderr.

To finish moving:

```sh
pip install hass-axi
hass-axi setup hooks      # repoints the session hooks the old name installed
pip uninstall ha-axi      # removes the forwarding command
```

`HA_URL` and `HA_TOKEN` are unchanged. `HA_AXI_READ_ONLY` and `HA_AXI_DEBUG` still work and are
deprecated in favour of `HASS_AXI_READ_ONLY` and `HASS_AXI_DEBUG`.

Source, changelog and issues: https://github.com/dmealing/hass-axi
