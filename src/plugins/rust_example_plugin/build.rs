use std::env;
use std::ffi::OsString;
use std::fmt;
use std::fs;
use std::io;
use std::path::{Path, PathBuf};
use std::process::{self, Command, ExitStatus, Stdio};

const WASM_TARGET: &str = "wasm32-unknown-unknown";
const MODULE_FILENAME: &str = "rust_example_plugin.wasm";

fn main() {
    if let Err(error) = build_module() {
        eprintln!("{error}");
        process::exit(error.exit_code());
    }
}

fn build_module() -> Result<(), BuildError> {
    let cargo = env::var_os("CARGO").unwrap_or_else(|| OsString::from("cargo"));
    let crate_root = Path::new(env!("CARGO_MANIFEST_DIR"));
    let child_target_dir = child_target_dir(crate_root);
    let output_path = output_path(crate_root)?;

    println!("Building Rust WASM module...");
    let status = Command::new(&cargo)
        .arg("build")
        .arg("--release")
        .arg("--target")
        .arg(WASM_TARGET)
        .arg("--lib")
        .arg("--target-dir")
        .arg(&child_target_dir)
        .current_dir(crate_root)
        .stdin(Stdio::inherit())
        .stdout(Stdio::inherit())
        .stderr(Stdio::inherit())
        .status()
        .map_err(|source| {
            if source.kind() == io::ErrorKind::NotFound {
                BuildError::CargoNotFound
            } else {
                BuildError::CargoStart(source)
            }
        })?;

    if !status.success() {
        return Err(BuildError::CargoFailed(status));
    }

    let built_module = built_module_path(&child_target_dir);
    if let Some(parent) = output_path.parent() {
        fs::create_dir_all(parent).map_err(|source| BuildError::CreateOutput {
            path: parent.to_path_buf(),
            source,
        })?;
    }
    fs::copy(&built_module, &output_path).map_err(|source| BuildError::CopyModule {
        source_path: built_module,
        output_path: output_path.clone(),
        source,
    })?;
    println!(
        "Copied Rust WebAssembly module to {}",
        output_path.display()
    );
    Ok(())
}

fn child_target_dir(crate_root: &Path) -> PathBuf {
    crate_root.join("target/wasm-build")
}

fn built_module_path(target_dir: &Path) -> PathBuf {
    target_dir
        .join(WASM_TARGET)
        .join("release")
        .join(MODULE_FILENAME)
}

fn output_path(crate_root: &Path) -> Result<PathBuf, BuildError> {
    let source_root = crate_root
        .parent()
        .ok_or_else(|| BuildError::InvalidCrateRoot(crate_root.to_path_buf()))?;
    Ok(source_root
        .join("resources/static/wasm")
        .join(MODULE_FILENAME))
}

#[derive(Debug)]
enum BuildError {
    CargoNotFound,
    CargoStart(io::Error),
    CargoFailed(ExitStatus),
    InvalidCrateRoot(PathBuf),
    CreateOutput {
        path: PathBuf,
        source: io::Error,
    },
    CopyModule {
        source_path: PathBuf,
        output_path: PathBuf,
        source: io::Error,
    },
}

impl BuildError {
    fn exit_code(&self) -> i32 {
        match self {
            Self::CargoFailed(status) => status.code().unwrap_or(1),
            _ => 1,
        }
    }
}

impl fmt::Display for BuildError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::CargoNotFound => write!(
                formatter,
                "Cargo was not found. Install Rust and the {WASM_TARGET} target first."
            ),
            Self::CargoStart(source) => write!(formatter, "Failed to execute Cargo: {source}"),
            Self::CargoFailed(status) => write!(formatter, "Cargo build failed with {status}"),
            Self::InvalidCrateRoot(path) => write!(
                formatter,
                "Rust plugin crate has no source-directory parent: {}",
                path.display()
            ),
            Self::CreateOutput { path, source } => write!(
                formatter,
                "Failed to create output directory {}: {source}",
                path.display()
            ),
            Self::CopyModule {
                source_path,
                output_path,
                source,
            } => write!(
                formatter,
                "Failed to copy {} to {}: {source}",
                source_path.display(),
                output_path.display()
            ),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn nested_build_uses_an_isolated_target_directory() {
        let crate_root = Path::new("src/rust_example_plugin");
        let target_dir = child_target_dir(crate_root);

        assert_eq!(target_dir, crate_root.join("target/wasm-build"));
        assert_eq!(
            built_module_path(&target_dir),
            target_dir.join(format!("{WASM_TARGET}/release/{MODULE_FILENAME}"))
        );
    }

    #[test]
    fn output_is_copied_to_static_resources() {
        assert_eq!(
            output_path(Path::new("src/rust_example_plugin")).unwrap(),
            PathBuf::from("src/resources/static/wasm/rust_example_plugin.wasm")
        );
    }
}
