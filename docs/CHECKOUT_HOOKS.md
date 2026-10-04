# Automatic checkout checks

Cadevil validates its source and package policy after Git updates a checkout. The same `scripts/check_source.py` validator runs during `uv build`. The checkout hook checks reviewed inputs, Docker exclusions and compiler inputs, source SBOM evidence, and plugin resource isolation. It uses the existing Python environment and performs no dependency installation, network access, Rust build, database bootstrap, or service startup.

The checker runs Python in isolated mode, which ignores Python environment overrides and local module search paths. Bytecode output is disabled.

`make install` prepares the normal development environment and installs the repository hook. For an environment that is already prepared, install the hook once:

```sh
make install-checkout-hook
```

The target uses `.venv/bin/python`. An existing interpreter can be selected explicitly:

```sh
make install-checkout-hook CHECKOUT_PYTHON=/absolute/path/to/python
```

Installation first validates the current checkout and then creates a dispatcher in the repository's default Git hooks directory. It leaves Git configuration and unrelated hooks unchanged. Running the installer again preserves the same dispatcher.

The installer verifies the hook directories, configuration and existing dispatcher again after validation. It publishes complete dispatcher bytes without replacing an existing file; concurrent changes cause installation to fail safely and can be reviewed before retrying.

The shared dispatcher resolves the active worktree each time, so `git checkout`, `git switch`, file checkouts, and `git worktree add` validate their own files. The hook uses `CADEVIL_CHECK_PYTHON` when set, then the checkout's `.venv`, the main checkout's existing `.venv`, or an existing `python3`. Python 3.13 or newer is required; a missing or incompatible interpreter is reported without installing one.

Git runs `post-checkout` after it has updated the files. A failed check returns a nonzero status and prints the failing policy, but the requested checkout has already happened and local changes remain. Correct the reported issue and rerun the check:

```sh
.venv/bin/python -I -B scripts/check_source.py --source .
```

Branches predating these checks fail clearly if the installed dispatcher cannot find their checked-in hook. They are not reset or modified by the dispatcher.

## Existing hook configurations

The installer refuses to replace an existing `post-checkout` hook or alter an existing `core.hooksPath`. Keep that configuration and add this call to the existing hook, preserving its other commands and failure handling:

```sh
checkout_root=$(git rev-parse --show-toplevel)
"$checkout_root/.githooks/post-checkout" "$@" || exit $?
```

Ensure the existing hook remains executable. For details on execution order, worktrees, exit status and custom paths, see the official [Git hook documentation](https://git-scm.com/docs/githooks#_post_checkout) and [core.hooksPath reference](https://git-scm.com/docs/git-config#Documentation/git-config.txt-corehooksPath).

The checked-in hook and its installer are developer tools. They are excluded from the production runtime and container build inputs.
