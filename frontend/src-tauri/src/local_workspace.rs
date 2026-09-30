//! Local notes, correction history and source-backed project vaults.
//! Revisions are immutable files; readers never observe an incomplete write.
use chrono::Utc;
use serde::{Deserialize, Serialize};
use std::{collections::HashSet, fs::{self, OpenOptions}, io::Write, path::{Path, PathBuf}, sync::Mutex};
use crate::state::AppState;
use tauri::Manager;

static STORE_LOCK: Mutex<()> = Mutex::new(());

pub fn data_root() -> Result<PathBuf, String> {
    let explicit=std::env::var_os("STTAPP_DATA_DIR").map(PathBuf::from);
    let installed=if explicit.is_none() { std::env::current_exe().ok().and_then(|exe|exe.parent().map(|p|p.join("sttapp-local-install.json")))
        .filter(|path|path.is_file()).map(|path|->Result<PathBuf,String> {
            let value:serde_json::Value=serde_json::from_slice(&fs::read(path).map_err(|e|e.to_string())?).map_err(|e|e.to_string())?;
            value["data_root"].as_str().map(PathBuf::from).ok_or("Local installation manifest is missing data_root".into())
        }).transpose()? } else { None };
    if let Some(root)=explicit.or(installed) {
        if !root.is_absolute() { return Err("STTAPP_DATA_DIR must be an absolute local directory".into()); }
        if root.components().any(|part|part.as_os_str().to_string_lossy().to_lowercase().starts_with("onedrive")) {
            return Err("Choose a local STTApp directory outside OneDrive".into());
        }
        return Ok(root);
    }
    dirs::data_local_dir().map(|p| p.join("STTApp")).ok_or("Local application data directory is unavailable".into())
}

pub fn previous_speaker_snapshot(id: &str) -> Result<Option<serde_json::Value>,String> {
    let _guard=STORE_LOCK.lock().map_err(|e|e.to_string())?;
    let dir=object_dir(&data_root()?,"workspaces",id)?;
    let value=with_job_metadata(&dir,latest_revision(&dir)?.unwrap_or_else(||empty_workspace(id)))?;
    let mut result=value["speaker_metadata"].clone();
    if let Some(speakers)=result["speakers"].as_array_mut() {
        for speaker in speakers {
            if let Some(name)=speaker["id"].as_str().and_then(|id|value["speaker_names"][id].as_str()) {
                speaker["display_name"]=name.into();
            }
        }
        Ok(Some(result))
    } else { Ok(None) }
}

pub fn valid_id(id: &str) -> Result<(), String> {
    if id.is_empty() || id.len() > 100 || !id.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'_') {
        return Err("Invalid local object ID".into());
    }
    Ok(())
}

fn object_dir(root: &Path, collection: &str, id: &str) -> Result<PathBuf, String> {
    valid_id(id)?;
    Ok(root.join(collection).join(id))
}

pub fn latest_revision(dir: &Path) -> Result<Option<serde_json::Value>, String> {
    if !dir.exists() { return Ok(None); }
    let mut versions = Vec::new();
    for item in fs::read_dir(dir).map_err(|e| e.to_string())? {
        let path = item.map_err(|e| e.to_string())?.path();
        let name = path.file_name().and_then(|s| s.to_str()).unwrap_or("");
        if let Some(version) = name.strip_prefix("revision-").and_then(|s| s.strip_suffix(".json")).and_then(|s| s.parse::<u64>().ok()) {
            versions.push((version, path));
        }
    }
    versions.sort_by_key(|v| v.0);
    match versions.last() {
        None => Ok(None),
        Some((revision, path)) => {
            let value: serde_json::Value = serde_json::from_slice(&fs::read(path).map_err(|e| e.to_string())?).map_err(|e| e.to_string())?;
            if value["revision"].as_u64() != Some(*revision) { return Err("Stored revision is inconsistent; previous files were preserved".into()); }
            Ok(Some(value))
        }
    }
}

fn append_revision(dir: &Path, expected: u64, mut value: serde_json::Value) -> Result<serde_json::Value, String> {
    let current = latest_revision(dir)?.and_then(|v| v["revision"].as_u64()).unwrap_or(0);
    if current != expected { return Err("This workspace changed in another window. Reload before saving.".into()); }
    let revision = current.checked_add(1).ok_or("Revision limit reached")?;
    value["revision"] = revision.into();
    value["updated_at"] = Utc::now().to_rfc3339().into();
    fs::create_dir_all(dir).map_err(|e| e.to_string())?;
    let temp = dir.join(format!("{}.part", uuid::Uuid::new_v4()));
    let final_path = dir.join(format!("revision-{revision:012}.json"));
    let mut file = OpenOptions::new().create_new(true).write(true).open(&temp).map_err(|e| e.to_string())?;
    file.write_all(&serde_json::to_vec_pretty(&value).map_err(|e| e.to_string())?).map_err(|e| e.to_string())?;
    file.sync_all().map_err(|e| e.to_string())?;
    drop(file);
    if final_path.exists() { return Err("Revision conflict; existing data was preserved".into()); }
    fs::rename(temp, final_path).map_err(|e| e.to_string())?;
    Ok(value)
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Correction {
    pub segment_id: String,
    pub original_text: String,
    pub text: String,
    pub updated_at: String,
}

fn empty_workspace(id: &str) -> serde_json::Value {
    serde_json::json!({"version":1,"meeting_id":id,"revision":0,"notes":"","corrections":[],"project_id":"tapf","profile":null})
}

fn with_job_metadata(dir: &Path, mut value: serde_json::Value) -> Result<serde_json::Value,String> {
    // Worker progress has its own immutable revision stream. It must not make
    // a user's unsaved notes/corrections stale every twenty seconds.
    if let Some(metadata)=latest_revision(&dir.join("job-metadata"))? {
        for key in ["source_job_id","source_meeting_id","profile","workflow_role","segment_metadata"] {
            value[key]=metadata[key].clone();
        }
    }
    if let Some(speakers)=latest_revision(&dir.join("speaker-metadata"))? {
        value["speaker_metadata"]=speakers["result"].clone();
        value["speaker_job_id"]=speakers["source_job_id"].clone();
    }
    Ok(value)
}

#[tauri::command]
pub async fn load_transcript_workspace(state: tauri::State<'_, AppState>, meeting_id: String) -> Result<serde_json::Value, String> {
    valid_id(&meeting_id)?;
    let legacy: Option<(Option<String>, Option<String>)> = sqlx::query_as("SELECT notes_markdown, notes_json FROM meeting_notes WHERE meeting_id = ?")
        .bind(&meeting_id).fetch_optional(state.db_manager.pool()).await.map_err(|e|e.to_string())?;
    let _guard = STORE_LOCK.lock().map_err(|e| e.to_string())?;
    let dir = object_dir(&data_root()?, "workspaces", &meeting_id)?;
    if let Some(value)=latest_revision(&dir)? { return with_job_metadata(&dir,value); }
    let mut value=empty_workspace(&meeting_id);
    if let Some((markdown,structured))=legacy {
        value["notes"]=markdown.unwrap_or_default().into();
        value["legacy_notes_json"]=serde_json::to_value(structured).map_err(|e|e.to_string())?;
        let saved=append_revision(&dir,0,value)?;
        return with_job_metadata(&dir,saved);
    }
    with_job_metadata(&dir,value)
}

#[tauri::command]
pub async fn save_transcript_workspace(
    state: tauri::State<'_, AppState>, meeting_id: String, expected_revision: u64,
    notes: String, corrections: Vec<Correction>, project_id: Option<String>, profile: Option<String>,
    speaker_names: Option<std::collections::HashMap<String,String>>,
) -> Result<serde_json::Value, String> {
    valid_id(&meeting_id)?;
    if notes.len() > 2_000_000 || corrections.len() > 20000 { return Err("Workspace is too large".into()); }
    if let Some(id) = &project_id { valid_id(id)?; }
    // Historical chunk profiles remain readable/editable even after new jobs
    // are restricted to the selected twenty-second settings.
    if let Some(id) = &profile { valid_id(id)?; }
    let mut ids = HashSet::new();
    for c in &corrections {
        if !ids.insert(&c.segment_id) || c.text.len() > 100_000 { return Err("Duplicate or oversized correction".into()); }
        let source: Option<String> = sqlx::query_scalar("SELECT transcript FROM transcripts WHERE meeting_id = ? AND id = ?")
            .bind(&meeting_id).bind(&c.segment_id).fetch_optional(state.db_manager.pool()).await.map_err(|e| e.to_string())?;
        if source.as_deref() != Some(c.original_text.as_str()) { return Err("A correction no longer matches its original transcript. Reload before saving.".into()); }
    }
    let _guard = STORE_LOCK.lock().map_err(|e| e.to_string())?;
    let dir = object_dir(&data_root()?, "workspaces", &meeting_id)?;
    let mut value = latest_revision(&dir)?.unwrap_or_else(|| empty_workspace(&meeting_id));
    if let Some(names)=speaker_names {
        if names.len()>100 || names.values().any(|name|name.len()>200) { return Err("Speaker names exceed local limits".into()); }
        for id in names.keys() { valid_id(id)?; }
        value["speaker_names"]=serde_json::to_value(names).map_err(|e|e.to_string())?;
    }
    value["notes"] = notes.into(); value["corrections"] = serde_json::to_value(corrections).map_err(|e|e.to_string())?;
    value["project_id"] = serde_json::to_value(project_id).map_err(|e|e.to_string())?;
    value["profile"] = serde_json::to_value(profile).map_err(|e|e.to_string())?;
    let saved=append_revision(&dir, expected_revision, value)?;
    with_job_metadata(&dir,saved)
}

pub fn set_speaker_metadata(id: &str, job: &str, result: &serde_json::Value) -> Result<(),String> {
    if !result["speakers"].is_array() || !result["turns"].is_array() || !result["segment_assignments"].is_object() {
        return Err("Speaker result is incomplete; prior names and assignments were preserved".into());
    }
    let _guard=STORE_LOCK.lock().map_err(|e|e.to_string())?;
    let dir=object_dir(&data_root()?,"workspaces",id)?.join("speaker-metadata");
    let previous=latest_revision(&dir)?;
    if previous.as_ref().map(|p|p["source_job_id"]==job && &p["result"]==result).unwrap_or(false) { return Ok(()); }
    let expected=previous.and_then(|p|p["revision"].as_u64()).unwrap_or(0);
    append_revision(&dir,expected,serde_json::json!({"version":1,"source_job_id":job,"result":result}))?;
    Ok(())
}

pub fn set_job_metadata(id: &str, parent: &str, job: &str, status: &serde_json::Value) -> Result<(),String> {
    let _guard=STORE_LOCK.lock().map_err(|e|e.to_string())?;
    let dir=object_dir(&data_root()?,"workspaces",id)?.join("job-metadata");
    let mut value=latest_revision(&dir)?.unwrap_or_else(||serde_json::json!({"version":1,"revision":0}));
    let mut metadata=serde_json::Map::new();
    for segment in status["segments"].as_array().ok_or("Missing segments")? {
        if let Some(sid)=segment["id"].as_str() {
            metadata.insert(sid.into(),serde_json::json!({"source_track":segment["source_track"],"timestamp_kind":"audio_window",
                "quality_flags":segment["quality_flags"],"alternative":segment["alternative"]}));
        }
    }
    if value["source_job_id"]==job && value["segment_metadata"]==serde_json::Value::Object(metadata.clone()) { return Ok(()); }
    let expected=value["revision"].as_u64().unwrap_or(0);
    value["source_job_id"]=job.into(); value["source_meeting_id"]=parent.into(); value["profile"]=status["profile"].clone();
    value["workflow_role"]=status["workflow_role"].clone();
    value["segment_metadata"]=metadata.into();
    append_revision(&dir,expected,value)?;
    Ok(())
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct VaultEntry {
    pub id: String, pub kind: String, pub canonical: String,
    #[serde(default)] pub aliases: Vec<String>,
    pub source: String, pub verified: bool,
    #[serde(default)] pub context: String,
    #[serde(default)] pub unit: String,
    #[serde(default)] pub valid_from: String,
    #[serde(default)] pub valid_to: String,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct VaultRelationship {
    pub subject_id: String, pub predicate: String, pub object_id: String,
    pub source: String, pub verified: bool,
}

fn validate_vault(entries: &[VaultEntry], relationships: &[VaultRelationship]) -> Result<(), String> {
    if entries.len() > 10000 || relationships.len() > 20000 { return Err("Vault exceeds local limits".into()); }
    let mut ids = HashSet::new();
    for entry in entries {
        valid_id(&entry.id)?;
        if !ids.insert(entry.id.as_str()) || entry.canonical.trim().is_empty() || entry.canonical.len() > 1000
            || !["person","organization","place","acronym","term","quantity"].contains(&entry.kind.as_str()) {
            return Err("Each vault entry needs a unique ID, supported type and name".into());
        }
        if entry.aliases.len() > 50 || entry.aliases.iter().any(|a| a.trim().is_empty() || a.len() > 1000) { return Err("Invalid aliases".into()); }
        if entry.source.len() > 10000 || entry.context.len() > 10000 { return Err("Vault source or context is too long".into()); }
        if entry.verified && entry.source.trim().is_empty() { return Err("Verified entries need a source".into()); }
        if entry.verified && entry.kind == "quantity" && (entry.context.trim().is_empty() || entry.unit.trim().is_empty()) {
            return Err("Verified quantities need units and a specific context; a number is not a universal correction".into());
        }
        for date in [&entry.valid_from, &entry.valid_to] {
            if !date.is_empty() && chrono::NaiveDate::parse_from_str(date, "%Y-%m-%d").is_err() { return Err("Validity dates must use YYYY-MM-DD".into()); }
        }
        if !entry.valid_from.is_empty() && !entry.valid_to.is_empty() && entry.valid_from > entry.valid_to { return Err("Validity dates are reversed".into()); }
    }
    for edge in relationships {
        if !ids.contains(edge.subject_id.as_str()) || !ids.contains(edge.object_id.as_str()) || edge.predicate.trim().is_empty()
            || (edge.verified && edge.source.trim().is_empty()) { return Err("Relationships need valid entry IDs, a predicate and a source when verified".into()); }
    }
    Ok(())
}

fn default_vault() -> serde_json::Value {
    serde_json::json!({"version":1,"id":"tapf","name":"TAPF Vault","revision":0,"entries":[],"relationships":[]})
}

#[tauri::command]
pub fn load_project_vault(project_id: String) -> Result<serde_json::Value, String> {
    let _guard = STORE_LOCK.lock().map_err(|e| e.to_string())?;
    let dir = object_dir(&data_root()?, "vaults", &project_id)?;
    match latest_revision(&dir)? {
        Some(value) => Ok(value),
        None if project_id == "tapf" => append_revision(&dir, 0, default_vault()),
        None => Err("Project vault does not exist".into()),
    }
}

#[tauri::command]
pub fn list_project_vaults() -> Result<Vec<serde_json::Value>, String> {
    // Ensure the user's requested empty TAPF Vault exists; no guessed facts.
    load_project_vault("tapf".into())?;
    let _guard = STORE_LOCK.lock().map_err(|e| e.to_string())?;
    let mut result = Vec::new();
    for entry in fs::read_dir(data_root()?.join("vaults")).map_err(|e| e.to_string())? {
        let path = entry.map_err(|e| e.to_string())?.path();
        if path.is_dir() { if let Some(vault) = latest_revision(&path)? { result.push(vault); } }
    }
    result.sort_by_key(|v| v["name"].as_str().unwrap_or("").to_owned());
    Ok(result)
}

#[tauri::command]
pub fn save_project_vault(project_id: String, expected_revision: u64, name: String,
    entries: Vec<VaultEntry>, relationships: Vec<VaultRelationship>) -> Result<serde_json::Value, String> {
    if name.trim().is_empty() || name.len() > 200 { return Err("A project vault needs a name".into()); }
    validate_vault(&entries, &relationships)?;
    let _guard = STORE_LOCK.lock().map_err(|e| e.to_string())?;
    append_revision(&object_dir(&data_root()?, "vaults", &project_id)?, expected_revision,
        serde_json::json!({"version":1,"id":project_id,"name":name,"entries":entries,"relationships":relationships}))
}

#[tauri::command]
pub async fn local_get_meeting_audio(app: tauri::AppHandle, state: tauri::State<'_, AppState>, meeting_id: String) -> Result<serde_json::Value, String> {
    let folder: Option<Option<String>> = sqlx::query_scalar("SELECT folder_path FROM meetings WHERE id = ?")
        .bind(meeting_id).fetch_optional(state.db_manager.pool()).await.map_err(|e| e.to_string())?;
    let audio = folder.flatten().and_then(|p| ["audio.mp4", "audio.wav", "mixed.wav", "microphone.wav", "system.wav"].iter()
        .map(|name| Path::new(&p).join(name)).find(|path| path.is_file()));
    if let Some(path) = &audio { app.asset_protocol_scope().allow_file(path).map_err(|e|e.to_string())?; }
    Ok(serde_json::json!({"path":audio.map(|p| p.to_string_lossy().to_string())}))
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn revisions_reject_conflicts_and_keep_prior_notes() {
        let root = tempfile::tempdir().unwrap();
        let dir = root.path().join("workspace");
        append_revision(&dir, 0, serde_json::json!({"notes":"first"})).unwrap();
        assert!(append_revision(&dir, 0, serde_json::json!({"notes":"stale"})).is_err());
        append_revision(&dir, 1, serde_json::json!({"notes":"second"})).unwrap();
        assert_eq!(latest_revision(&dir).unwrap().unwrap()["notes"], "second");
        assert!(fs::read_to_string(dir.join("revision-000000000001.json")).unwrap().contains("first"));
        fs::write(dir.join("orphan.part"), b"broken").unwrap();
        assert_eq!(latest_revision(&dir).unwrap().unwrap()["revision"], 2);
        assert!(valid_id("../other").is_err());
    }
    #[test]
    fn progress_metadata_does_not_invalidate_unsaved_notes() {
        let root=tempfile::tempdir().unwrap();
        let dir=root.path().join("workspace");
        append_revision(&dir,0,serde_json::json!({"notes":"before","corrections":[]})).unwrap();
        append_revision(&dir.join("job-metadata"),0,serde_json::json!({"source_job_id":"job","source_meeting_id":"meeting","profile":"trelis-20","segment_metadata":{"chunk1":{"source_track":"system"}}})).unwrap();
        let saved=append_revision(&dir,1,serde_json::json!({"notes":"user draft","corrections":[]})).unwrap();
        let loaded=with_job_metadata(&dir,saved).unwrap();
        assert_eq!(loaded["revision"],2);
        assert_eq!(loaded["notes"],"user draft");
        assert_eq!(loaded["segment_metadata"]["chunk1"]["source_track"],"system");
        assert!(append_revision(&dir,1,serde_json::json!({"notes":"stale edit"})).is_err());
    }
    #[test]
    fn verified_quantity_requires_evidence_context_and_unit() {
        let mut entry = VaultEntry { id:"q".into(),kind:"quantity".into(),canonical:"7".into(),aliases:vec![],
            source:"".into(),verified:true,context:"".into(),unit:"".into(),valid_from:"".into(),valid_to:"".into() };
        assert!(validate_vault(&[entry.clone()], &[]).is_err());
        entry.source="checked document".into(); entry.context="notebooks per workshop kit".into(); entry.unit="notebooks".into();
        assert!(validate_vault(&[entry.clone()], &[]).is_ok());
        entry.valid_from="2026-10-01".into(); entry.valid_to="2026-09-01".into();
        assert!(validate_vault(&[entry], &[]).is_err());
    }
}
