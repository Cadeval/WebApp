use super::*;

#[test]
fn nested_build_uses_an_isolated_target_directory() {
    let crate_root = Path::new("plugins/example_plugin");
    let target_dir = child_target_dir(crate_root);

    assert_eq!(target_dir, crate_root.join("target/wasm-build"));
    assert_eq!(
        built_module_path(&target_dir),
        target_dir.join(format!("{WASM_TARGET}/release/{MODULE_FILENAME}"))
    );
}

#[test]
fn output_is_copied_to_plugin_static_resources() {
    assert_eq!(
        output_path(Path::new("plugins/example_plugin")),
        PathBuf::from("plugins/example_plugin/static/wasm/example_plugin.wasm")
    );
}
