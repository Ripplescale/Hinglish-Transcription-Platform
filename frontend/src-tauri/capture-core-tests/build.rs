// Compile exact non-command workspace items without the Tauri/device graph.
// This is a source-component test, not an application typecheck.
use quote::quote;
use std::{env, fs, path::PathBuf};

fn main() {
    let source = PathBuf::from(env::var("CARGO_MANIFEST_DIR").unwrap())
        .join("../src/local_workspace.rs");
    println!("cargo:rerun-if-changed={}", source.display());
    let parsed = syn::parse_file(&fs::read_to_string(source).unwrap()).unwrap();
    let items: Vec<_> = parsed.items.into_iter().filter(|item| match item {
        syn::Item::Fn(function) => !function.attrs.iter().any(|attribute| {
            let names: Vec<_> = attribute.path().segments.iter().map(|s| s.ident.to_string()).collect();
            names == ["tauri", "command"]
        }),
        syn::Item::Use(import) => {
            let tree = &import.tree;
            let name = quote!(#tree).to_string();
            name != "crate :: state :: AppState" && name != "tauri :: Manager"
        }
        _ => true,
    }).collect();
    let output = PathBuf::from(env::var("OUT_DIR").unwrap()).join("workspace_components.rs");
    fs::write(output, quote!(#(#items)*).to_string()).unwrap();
    let transcription = PathBuf::from(env::var("CARGO_MANIFEST_DIR").unwrap()).join("../src/local_transcription.rs");
    println!("cargo:rerun-if-changed={}", transcription.display());
    let parsed = syn::parse_file(&fs::read_to_string(transcription).unwrap()).unwrap();
    let lifecycle = parsed.items.iter().find(|item| matches!(item, syn::Item::Fn(function) if function.sig.ident == "project_worker_lifecycle")).unwrap();
    fs::write(PathBuf::from(env::var("OUT_DIR").unwrap()).join("worker_lifecycle.rs"), quote!(#lifecycle).to_string()).unwrap();
    let helpers: Vec<_> = parsed.items.iter().filter(|item| matches!(item,
        syn::Item::Fn(function) if ["read", "capture_ready_for_final", "validate_workflow_role"].contains(&function.sig.ident.to_string().as_str())
    )).collect();
    assert_eq!(helpers.len(), 3, "Automatic-workflow helpers must be extracted from production source");
    let tests: Vec<_> = parsed.items.iter().filter_map(|item| match item {
        syn::Item::Mod(module) if module.ident == "tests" => module.content.as_ref().map(|(_, items)| items),
        _ => None,
    }).flatten().filter(|item| matches!(item,
        syn::Item::Fn(function) if ["automatic_roles_do_not_admit_swapped_models", "final_requires_committed_completion_or_matching_recovery"].contains(&function.sig.ident.to_string().as_str())
    )).collect();
    assert_eq!(tests.len(), 2, "Automatic-workflow assertions must stay linked to production tests");
    fs::write(PathBuf::from(env::var("OUT_DIR").unwrap()).join("automatic_workflow.rs"), quote!(#(#helpers)* #(#tests)*).to_string()).unwrap();
    let snapshot_helpers: Vec<_> = parsed.items.iter().filter(|item| matches!(item,
        syn::Item::Fn(function) if ["read", "validate_asr_runtime", "ensure_job_runtime_snapshot"].contains(&function.sig.ident.to_string().as_str())
    )).collect();
    assert_eq!(snapshot_helpers.len(), 3, "Runtime snapshot helpers must be extracted from production source");
    let snapshot_tests: Vec<_> = parsed.items.iter().filter_map(|item| match item {
        syn::Item::Mod(module) if module.ident == "tests" => module.content.as_ref().map(|(_, items)| items),
        _ => None,
    }).flatten().filter(|item| matches!(item,
        syn::Item::Fn(function) if ["synthetic_asr_runtime", "asr_runtime_snapshot_keeps_old_runtime_after_global_change",
            "asr_runtime_snapshot_rejects_invalid_or_missing_files_without_replacement",
            "legacy_asr_checkpoint_requires_original_runtime_migration", "concurrent_asr_snapshot_creation_has_one_complete_winner"
        ].contains(&function.sig.ident.to_string().as_str())
    )).collect();
    assert_eq!(snapshot_tests.len(), 5, "Runtime snapshot assertions must stay linked to production tests");
    fs::write(PathBuf::from(env::var("OUT_DIR").unwrap()).join("runtime_snapshots.rs"), quote!(#(#snapshot_helpers)* #(#snapshot_tests)*).to_string()).unwrap();
}
