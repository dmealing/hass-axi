# Changelog

This project was published as `ha-axi` up to and including 0.7.1, and as `hass-axi` from the
release after it. The entries below 0.7.1 describe `ha-axi`; their links point at the repository's
old name, which GitHub redirects.

## [0.7.1](https://github.com/dmealing/ha-axi/compare/v0.7.0...v0.7.1) (2026-08-29)


### Documentation

* **releasing:** record why a non-user-facing commit cuts no release and how to force one ([#32](https://github.com/dmealing/ha-axi/issues/32)) ([4d78ff9](https://github.com/dmealing/ha-axi/commit/4d78ff93c25acdd9ac5204a691b218b3d917b8df))


### Build System

* **scripts:** make the documented developer setup isolated by construction ([#31](https://github.com/dmealing/ha-axi/issues/31)) ([0906f61](https://github.com/dmealing/ha-axi/commit/0906f61c28ad5e30f719b3aa1ad67e7fef96c5e0)). Setting up a checkout is `scripts/dev-setup.sh` now: it builds `.venv` and prints the `.venv/bin/<tool>` forms every development command runs out of. The block it replaces installed the checkout, editable, into whatever interpreter happened to be ambient, which overwrites the launcher of an existing isolated install of this tool and leaves it dead once the checkout is deleted, with nothing announcing it. Nothing in the shipped tool behaves differently.

## [0.7.0](https://github.com/dmealing/ha-axi/compare/v0.6.0...v0.7.0) (2026-08-29)


### Features

* **device:** add get and update subcommands ([#29](https://github.com/dmealing/ha-axi/issues/29)) ([1e7ad10](https://github.com/dmealing/ha-axi/commit/1e7ad10207a3ea7ea7a669f632a5514e4ef088d8))

## [0.6.0](https://github.com/dmealing/ha-axi/compare/v0.5.1...v0.6.0) (2026-08-29)


### Features

* **context:** give the session hook a document it can print with nothing configured ([#27](https://github.com/dmealing/ha-axi/issues/27)) ([da22501](https://github.com/dmealing/ha-axi/commit/da225015a116c7a4e4e9c296620c9ad2791aea8d))


### Bug Fixes

* **hooks:** leave a session hook this tool did not write alone, instead of overwriting it and reporting installed ([#27](https://github.com/dmealing/ha-axi/issues/27)) ([da22501](https://github.com/dmealing/ha-axi/commit/da225015a116c7a4e4e9c296620c9ad2791aea8d))
* **hooks:** repair every stale hook entry instead of stopping at the first one and reporting the target current ([#27](https://github.com/dmealing/ha-axi/issues/27)) ([da22501](https://github.com/dmealing/ha-axi/commit/da225015a116c7a4e4e9c296620c9ad2791aea8d))
* **hooks:** rewrite the Codex `hooks` key whatever its value, instead of appending a duplicate one its own parser rejects ([#27](https://github.com/dmealing/ha-axi/issues/27)) ([da22501](https://github.com/dmealing/ha-axi/commit/da225015a116c7a4e4e9c296620c9ad2791aea8d))

## [0.5.1](https://github.com/dmealing/ha-axi/compare/v0.5.0...v0.5.1) (2026-08-29)


### Bug Fixes

* **ws:** enter the websockets connection instead of assigning it ([#25](https://github.com/dmealing/ha-axi/issues/25)) ([bb2593a](https://github.com/dmealing/ha-axi/commit/bb2593a59d67bfc8409f6c37b6b86c5cbda19514))


### Dependencies

* **axi-toolkit:** now a runtime dependency alongside `websockets` ([#24](https://github.com/dmealing/ha-axi/issues/24)) ([12fbc6f](https://github.com/dmealing/ha-axi/commit/12fbc6fbdf586eecd74e2baf42ab19666d88eb86)). It is where the service model reader lives now, so there is one copy of it rather than two, and it declares no dependencies of its own, so the installed closure grows by exactly one.

## [0.5.0](https://github.com/dmealing/ha-axi/compare/v0.4.0...v0.5.0) (2026-08-23)


### Features

* **errors:** a closed error vocabulary, classified at the transport ([e4200c6](https://github.com/dmealing/ha-axi/commit/e4200c65981c37373de16b3c52de3bdf30d774a5))


### Documentation

* lead with the registries and service-call judgement, not the commodity half ([5dbf68e](https://github.com/dmealing/ha-axi/commit/5dbf68e77848a6f78d2ee7fd6ec804820617b755))

## [0.4.0](https://github.com/dmealing/ha-axi/compare/v0.3.4...v0.4.0) (2026-08-23)


### Features

* **cli:** a read-only mode that holds on both transports, and fails closed ([91a574b](https://github.com/dmealing/ha-axi/commit/91a574b5b64555f0e03e29152dd62682a43f4617))

## [0.3.4](https://github.com/dmealing/ha-axi/compare/v0.3.3...v0.3.4) (2026-08-22)


### Bug Fixes

* **ci:** scan the pull request title and body, the surface no hook reaches ([#16](https://github.com/dmealing/ha-axi/issues/16)) ([cf0f812](https://github.com/dmealing/ha-axi/commit/cf0f812b28c96a53fff26417cc94c7be6ad2c8fb))

## [0.3.3](https://github.com/dmealing/ha-axi/compare/v0.3.2...v0.3.3) (2026-08-22)


### Bug Fixes

* **commands:** name entities the way Home Assistant does, plus a live audit's fixes ([#14](https://github.com/dmealing/ha-axi/issues/14)) ([0014b97](https://github.com/dmealing/ha-axi/commit/0014b9787976373808c59763d29c898d152212a0))

## [0.3.2](https://github.com/dmealing/ha-axi/compare/v0.3.1...v0.3.2) (2026-08-22)


### Bug Fixes

* **ci:** check the artefact release-please actually reads, not the one git has ([#13](https://github.com/dmealing/ha-axi/issues/13)) ([40767d9](https://github.com/dmealing/ha-axi/commit/40767d982c5bfa1732326a179efde28499e7ba83))
* **ci:** fail a release that silently dropped a commit, and reject the message that causes it ([#11](https://github.com/dmealing/ha-axi/issues/11)) ([e9a8ef5](https://github.com/dmealing/ha-axi/commit/e9a8ef5e9d0fbaf5b178fa9fdd119bd5c11b110f))

## [0.3.1](https://github.com/dmealing/ha-axi/compare/v0.3.0...v0.3.1) (2026-08-21)


### Bug Fixes

* **toon:** satisfy both TOON spec MUST violations, enforced by vendored fixtures ([#9](https://github.com/dmealing/ha-axi/issues/9)) ([46c25f9](https://github.com/dmealing/ha-axi/commit/46c25f9916b7b19dddc297dfcdeae8630158a508))

## [0.3.0](https://github.com/dmealing/ha-axi/compare/v0.2.0...v0.3.0) (2026-08-19)


### Features

* **service:** explain refusals, add service get, pre-check capabilities ([#7](https://github.com/dmealing/ha-axi/issues/7)) ([05a6cb0](https://github.com/dmealing/ha-axi/commit/05a6cb08449dcde05aaf5eb1f02ebd249e836666))

## [0.2.0](https://github.com/dmealing/ha-axi/compare/v0.1.0...v0.2.0) (2026-08-19)


### Features

* report real areas from entity update and add --area to state list ([#5](https://github.com/dmealing/ha-axi/issues/5)) ([13423c1](https://github.com/dmealing/ha-axi/commit/13423c15050011e3ac8a7aecfc056940ef284e81))

## 0.1.0 (2026-08-19)


### Features

* ha-axi, an AXI CLI for the Home Assistant REST and WebSocket APIs ([28015a9](https://github.com/dmealing/ha-axi/commit/28015a90b36fe48ec29be37798849ba70ef04e5c))
