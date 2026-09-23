# LIFX Ultimate Release and README Design

## Purpose

Publish LIFX Ultimate as a conventional, versioned HACS integration while
keeping all authored feature documentation with the LIFX source in `ha-core`.
The generated HACS repository remains a deployment artifact.

## Versioning and releases

The source integration manifest remains the authoritative upstream-compatible
version and will use `2026.7.2`, with no custom suffix. The export process
reads that value and produces the HACS manifest version
`<source-version>-v0.0.<counter>`.

The counter is global and monotonically increasing across source-version
changes. Since three exports already exist, the first export using this scheme
will be `2026.7.2-v0.0.4`. A manual workflow run that has no generated content
change must not create a new commit, tag, release, or increment.

When an export changes the distribution, the publisher will commit the
generated files and push an annotated tag named exactly as the new exported
version. The HACS repository receives the tag push and a target-repository
workflow creates a GitHub Release for that tag with GitHub-generated release
notes. The existing deploy key remains limited to the target repository and is
used only to push generated commits and tags; the target workflow creates its
own release with its repository-scoped `GITHUB_TOKEN`.

## Generated README ownership

`homeassistant/components/lifx/README_FEATURES.md` will be the source-owned
feature-guide fragment. It is not runtime integration code and will be excluded
from `custom_components/lifx` in the HACS package. The exporter will generate
the root HACS `README.md` from a concise static header plus this fragment.

The static header includes the LIFX Ultimate title, installation steps, the
technical `lifx` domain compatibility note, and one normal paragraph titled
“📦 Automated publishing”. That paragraph identifies the source revision and
the generated version without presenting publishing mechanics as user update
instructions.

The feature fragment will use clear Markdown sections and light emoji accents:

- “✨ Why LIFX Ultimate?” explains the author’s appreciation for LIFX lights,
  their value and bright colors, and the two problems addressed: unsynchronised
  multi-light control and missing general transition defaults.
- “✅ Backward compatible” explains that the integration still uses the
  `lifx` domain, so existing LIFX configuration entries and normal Home
  Assistant LIFX usage continue to work.
- “🎯 Device Groups” explains that a Device Group is a virtual LIFX light made
  from selected existing LIFX light entities. It gives the configuration path:
  Settings → Devices & services → Add integration → LIFX Ultimate → Add Device
  Group. It explains in simple terms that workers prepare commands first and
  then release them together against a shared monotonic deadline, reducing
  network-send skew to millisecond-scale timing. It must not promise identical
  physical light rendering, which remains device and network dependent.
- “🌈 Fade and transition defaults” describes Fade On Time, Fade Off Time, and
  Cross Fade Time for both physical lights and Device Groups.
- “📐 Transition priority” gives the exact precedence rules below.

## Transition priority

The README will describe the existing implementation without changing lighting
behavior:

1. A `transition` provided in the current service call always wins, including
   an explicit `0`.
2. For a Device Group, the matching non-zero group number setting wins.
3. If that group setting is `0`, the matching setting on each physical member
   is used. Consequently, members can have different durations unless a group
   override is configured.
4. For a physical LIFX light, the matching non-zero light setting is used.
5. If no setting supplies a duration, the command is immediate.

The matching setting is Fade On Time when a light turns on or leaves virtual
off, Fade Off Time when it turns off, and Cross Fade Time when an already-on
light changes color or brightness without a power-state transition.

## Exporter interfaces and validation

The exporter will no longer receive a final version from the source workflow.
It will accept the source path, distribution path, source revision, and the
current counter. It will read the source manifest version, validate it is
present, and return or write the derived version consistently to the exported
manifest and README.

Exporter tests will cover the source-version suffix, the feature-fragment
append behavior, the exclusion of the fragment from the installed integration,
and preservation of the target `.git` metadata. Workflow validation will cover
the derived version and release/tag behavior through shell-level assertions
where practical; the live publisher and target release workflows will be
manually verified after push.
