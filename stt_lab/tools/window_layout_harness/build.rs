use std::{env, fs, path::PathBuf};

fn main() {
    let source = env::var_os("XX_WINDOW_LAYOUT_SOURCE")
        .map(PathBuf::from)
        .unwrap_or_else(|| {
            PathBuf::from(env::var_os("CARGO_MANIFEST_DIR").unwrap())
                .join("../../../frontend/src-tauri/src/window_layout.rs")
        })
        .canonicalize()
        .expect("The exact application window_layout.rs must exist");
    let output = PathBuf::from(env::var_os("OUT_DIR").unwrap());
    println!("cargo:rerun-if-env-changed=XX_WINDOW_LAYOUT_SOURCE");
    println!("cargo:rerun-if-changed={}", source.display());
    // A path module retains the source's inner documentation and cfg attributes.
    fs::write(
        output.join("window_layout_link.rs"),
        format!("#[path = {:?}]\nmod window_layout;\n", source),
    )
    .unwrap();
    // Match Tauri's Common Controls v6 activation context for named subclass APIs.
    let manifest = output.join("window-layout-harness.manifest");
    fs::write(&manifest, r#"<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<assembly xmlns="urn:schemas-microsoft-com:asm.v1" manifestVersion="1.0">
  <assemblyIdentity version="1.0.0.0" processorArchitecture="*" name="xx.WindowLayoutHarness" type="win32" />
  <dependency><dependentAssembly>
    <assemblyIdentity type="win32" name="Microsoft.Windows.Common-Controls" version="6.0.0.0" processorArchitecture="*" publicKeyToken="6595b64144ccf1df" language="*" />
  </dependentAssembly></dependency>
</assembly>"#).unwrap();
    println!("cargo:rustc-link-arg=/MANIFEST:EMBED");
    println!("cargo:rustc-link-arg=/MANIFESTINPUT:{}", manifest.display());
}
