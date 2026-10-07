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
    serde_json::json!({"version":1,"meeting_id":id,"revision":0,"notes":"","corrections":[],"project_id":null,"profile":null})
}

fn with_job_metadata(dir: &Path, mut value: serde_json::Value) -> Result<serde_json::Value,String> {
    // Worker progress has its own immutable revision stream. It must not make
    // a user's unsaved notes/corrections stale every twenty seconds.
    if let Some(metadata)=latest_revision(&dir.join("job-metadata"))? {
        for key in ["source_job_id","source_meeting_id","profile","workflow_role","segment_metadata","superseded_segments","segments_revision"] {
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
        // An archived correction remains a saved reference. The frontend only
        // applies it when its original_text exactly matches current raw text.
        if !crate::local_transcription::correction_source_matches(state.db_manager.pool(),&meeting_id,&c.segment_id,&c.original_text).await? {
            return Err("A correction no longer matches a verified transcript source. Reload before saving.".into());
        }
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

/// Autosaving a note must never replace transcript corrections or speaker names
/// with a stale copy held by a notes-only editor.
fn save_meeting_note_at(root: &Path, meeting_id: &str, expected_revision: u64,
    notes: String, project_id: Option<String>, legacy: Option<(Option<String>, Option<String>)>,
) -> Result<serde_json::Value, String> {
    valid_id(meeting_id)?;
    if notes.len() > 2_000_000 { return Err("Note is too large".into()); }
    if let Some(id) = &project_id {
        if latest_revision(&object_dir(root, "vaults", id)?)?.is_none() {
            return Err("Project no longer exists. Choose a project before saving.".into());
        }
    }
    let dir = object_dir(root, "workspaces", meeting_id)?;
    let current = latest_revision(&dir)?;
    let mut value = current.clone().unwrap_or_else(|| empty_workspace(meeting_id));
    if current.is_none() {
        if let Some((markdown, structured)) = legacy {
            value["notes"] = markdown.unwrap_or_default().into();
            value["legacy_notes_json"] = serde_json::to_value(structured).map_err(|e|e.to_string())?;
        }
    }
    if value["revision"].as_u64().unwrap_or(0) != expected_revision {
        return Err("This workspace changed in another window. Reload before saving.".into());
    }
    let project = serde_json::to_value(project_id).map_err(|e|e.to_string())?;
    if current.is_some() && value["notes"] == notes && value["project_id"] == project {
        return with_job_metadata(&dir, value);
    }
    value["notes"] = notes.into();
    value["project_id"] = project;
    with_job_metadata(&dir, append_revision(&dir, expected_revision, value)?)
}

#[tauri::command]
pub async fn save_meeting_note(state: tauri::State<'_, AppState>, meeting_id: String,
    expected_revision: u64, notes: String, project_id: Option<String>,
) -> Result<serde_json::Value, String> {
    valid_id(&meeting_id)?;
    let exists: i64 = sqlx::query_scalar("SELECT count(*) FROM meetings WHERE id = ?")
        .bind(&meeting_id).fetch_one(state.db_manager.pool()).await.map_err(|e|e.to_string())?;
    if exists == 0 { return Err("Conversation no longer exists. Your draft was not saved.".into()); }
    let legacy: Option<(Option<String>, Option<String>)> = sqlx::query_as("SELECT notes_markdown, notes_json FROM meeting_notes WHERE meeting_id = ?")
        .bind(&meeting_id).fetch_optional(state.db_manager.pool()).await.map_err(|e|e.to_string())?;
    let _guard = STORE_LOCK.lock().map_err(|e| e.to_string())?;
    save_meeting_note_at(&data_root()?, &meeting_id, expected_revision, notes, project_id, legacy)
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
    set_job_metadata_at(&data_root()?,id,parent,job,status)
}

fn set_job_metadata_at(root: &Path, id: &str, parent: &str, job: &str, status: &serde_json::Value) -> Result<(),String> {
    let dir=object_dir(root,"workspaces",id)?.join("job-metadata");
    let mut value=latest_revision(&dir)?.unwrap_or_else(||serde_json::json!({"version":1,"revision":0}));
    if let Some(previous_job)=value["source_job_id"].as_str() {
        if previous_job!=job { return Err("Workspace metadata belongs to another job".into()); }
        let old=value["segments_revision"].as_u64();
        let new=status["segments_revision"].as_u64();
        if old.is_some() && (new.is_none() || new<old) { return Ok(()); }
        if old==new && value["worker_updated_at"].as_str().zip(status["updated_at"].as_str())
            .and_then(|(a,b)|Some((chrono::DateTime::parse_from_rfc3339(a).ok()?,chrono::DateTime::parse_from_rfc3339(b).ok()?)))
            .map(|(a,b)|b<a).unwrap_or(false) { return Ok(()); }
    }
    let mut metadata=serde_json::Map::new();
    for segment in status["segments"].as_array().ok_or("Missing segments")? {
        if let Some(sid)=segment["id"].as_str() {
            let mut item=serde_json::json!({"source_track":segment["source_track"],"timestamp_kind":"audio_window",
                "quality_flags":segment["quality_flags"],"alternative":segment["alternative"],
                "recovery":segment["recovery"],"replaces_segment_ids":segment["replaces_segment_ids"]});
            if let Some(original)=segment["recognition_original"]["text"].as_str() {
                if segment["alternative"]["promoted"]==true || segment["text"].as_str().map(|text|text!=original).unwrap_or(false) {
                    item["original_recognition_text"]=original.into();
                }
            }
            metadata.insert(sid.into(),item);
        }
    }
    let archive=status.get("superseded_segments").cloned().unwrap_or_else(||serde_json::json!([]));
    if !archive.is_array() { return Err("Invalid superseded transcript metadata".into()); }
    if value["source_job_id"]==job && value["segment_metadata"]==serde_json::Value::Object(metadata.clone())
        && value["superseded_segments"]==archive && value["segments_revision"]==status["segments_revision"]
        && value["worker_updated_at"]==status["updated_at"] { return Ok(()); }
    let expected=value["revision"].as_u64().unwrap_or(0);
    value["source_job_id"]=job.into(); value["source_meeting_id"]=parent.into(); value["profile"]=status["profile"].clone();
    value["workflow_role"]=status["workflow_role"].clone();
    value["segment_metadata"]=metadata.into();
    value["superseded_segments"]=archive;
    value["segments_revision"]=status["segments_revision"].clone();
    value["worker_updated_at"]=status["updated_at"].clone();
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

fn project_summary(vault: &serde_json::Value) -> serde_json::Value {
    serde_json::json!({"id":vault["id"],"name":vault["name"],"revision":vault["revision"],
        "entry_count":vault["entries"].as_array().map(|entries|entries.len()).unwrap_or(0)})
}

fn validate_project_name(name: &str) -> Result<String, String> {
    let name = name.trim();
    if name.is_empty() || name.len() > 200 || name.chars().any(char::is_control) {
        return Err("Choose a project name between 1 and 200 bytes without control characters".into());
    }
    Ok(name.to_owned())
}

fn create_project_at(root: &Path, name: &str) -> Result<serde_json::Value, String> {
    let name = validate_project_name(name)?;
    let id = uuid::Uuid::new_v4().to_string();
    let value = append_revision(&object_dir(root, "vaults", &id)?, 0,
        serde_json::json!({"version":1,"id":id,"name":name,"entries":[],"relationships":[]}))?;
    Ok(project_summary(&value))
}

fn rename_project_at(root: &Path, project_id: &str, expected_revision: u64, name: &str) -> Result<serde_json::Value, String> {
    let name = validate_project_name(name)?;
    let dir = object_dir(root, "vaults", project_id)?;
    let mut value = latest_revision(&dir)?.ok_or("Project no longer exists")?;
    value["name"] = name.into();
    Ok(project_summary(&append_revision(&dir, expected_revision, value)?))
}

#[tauri::command]
pub fn create_project(name: String) -> Result<serde_json::Value, String> {
    let _guard = STORE_LOCK.lock().map_err(|e| e.to_string())?;
    create_project_at(&data_root()?, &name)
}

#[tauri::command]
pub fn rename_project(project_id: String, expected_revision: u64, name: String) -> Result<serde_json::Value, String> {
    let _guard = STORE_LOCK.lock().map_err(|e| e.to_string())?;
    rename_project_at(&data_root()?, &project_id, expected_revision, &name)
}

fn library_meeting(root: &Path, id: String, title: String, created_at: String, legacy_notes: Option<String>) -> Result<serde_json::Value, String> {
    // Only materialized assignments count. Loading a legacy/default workspace
    // must not silently file unrelated recordings under the initial vault.
    let workspace = latest_revision(&object_dir(root, "workspaces", &id)?)?;
    let notes = workspace.as_ref().and_then(|value|value["notes"].as_str())
        .or(legacy_notes.as_deref()).unwrap_or("");
    let preview = notes.split_whitespace().collect::<Vec<_>>().join(" ").chars().take(180).collect::<String>();
    Ok(serde_json::json!({"id":id,"title":title,"created_at":created_at,
        "project_id":workspace.as_ref().map(|value|value["project_id"].clone()).unwrap_or(serde_json::Value::Null),
        "notes_preview":preview}))
}

#[tauri::command]
pub async fn list_project_library(state: tauri::State<'_, AppState>) -> Result<serde_json::Value, String> {
    let rows: Vec<(String, String, String, Option<String>)> = sqlx::query_as(
        "SELECT m.id,m.title,m.created_at,n.notes_markdown FROM meetings m LEFT JOIN meeting_notes n ON n.meeting_id=m.id ORDER BY m.created_at DESC,m.id")
        .fetch_all(state.db_manager.pool()).await.map_err(|e|e.to_string())?;
    let grouped = crate::local_transcription::get_local_transcript_groups(state).await?;
    let hidden: HashSet<String> = grouped.iter().filter_map(|group|group["meeting_id"].as_str().map(str::to_owned)).collect();
    let projects = list_project_vaults()?.iter().map(project_summary).collect::<Vec<_>>();
    let _guard = STORE_LOCK.lock().map_err(|e| e.to_string())?;
    let root = data_root()?;
    let meetings = rows.into_iter().filter(|(id,_,_,_)|!hidden.contains(id))
        .map(|(id,title,created_at,legacy)|library_meeting(&root,id,title,created_at,legacy))
        .collect::<Result<Vec<_>,_>>()?;
    Ok(serde_json::json!({"projects":projects,"meetings":meetings}))
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
    fn promoted_retry_metadata_keeps_original_without_revising_notes_or_legacy_jobs() {
        let root=tempfile::tempdir().unwrap();
        let dir=root.path().join("workspaces").join("meeting-one");
        let saved=append_revision(&dir,0,serde_json::json!({"notes":"user notes","corrections":[]})).unwrap();
        let status=serde_json::json!({"profile":"trelis-10","workflow_role":"live-final","segments":[{
            "id":"chunk-one","text":"recovered speech","source_track":"system",
            "quality_flags":["retry_applied","needs_review"],
            "alternative":{"text":"recovered speech","promoted":true},
            "recognition_original":{"text":"जब जब जब जब जब जब जब जब जब जब जब जब"}
        }]});
        set_job_metadata_at(root.path(),"meeting-one","meeting-one","job-one",&status).unwrap();
        let loaded=with_job_metadata(&dir,saved.clone()).unwrap();
        assert_eq!(loaded["notes"],"user notes");
        assert_eq!(loaded["revision"],1);
        assert_eq!(loaded["segment_metadata"]["chunk-one"]["original_recognition_text"],status["segments"][0]["recognition_original"]["text"]);
        assert_eq!(loaded["segment_metadata"]["chunk-one"]["alternative"]["promoted"],true);
        let metadata_dir=dir.join("job-metadata");
        set_job_metadata_at(root.path(),"meeting-one","meeting-one","job-one",&status).unwrap();
        assert_eq!(latest_revision(&metadata_dir).unwrap().unwrap()["revision"],1);
        assert_eq!(latest_revision(&dir).unwrap().unwrap(),saved);

        let legacy=serde_json::json!({"profile":"trelis-20","segments":[{"id":"old-chunk",
            "source_track":"microphone","quality_flags":["retry_available"],
            "alternative":{"text":"comparison only","requires_review":true}}]});
        set_job_metadata_at(root.path(),"meeting-two","meeting-two","old-job",&legacy).unwrap();
        let old=latest_revision(&root.path().join("workspaces/meeting-two/job-metadata")).unwrap().unwrap();
        assert_eq!(old["segment_metadata"]["old-chunk"]["alternative"],legacy["segments"][0]["alternative"]);
        assert!(old["segment_metadata"]["old-chunk"].get("original_recognition_text").is_none());
    }
    #[test]
    fn canonical_recovery_metadata_keeps_archives_and_rejects_stale_revision_without_changing_notes() {
        let root=tempfile::tempdir().unwrap();
        let dir=root.path().join("workspaces/meeting-one");
        let saved=append_revision(&dir,0,serde_json::json!({"notes":"retained notes","corrections":[{
            "segment_id":"job-microphone-0","original_text":"old loop","text":"manual correction","updated_at":"earlier"
        }]})).unwrap();
        let archive=serde_json::json!([{"id":"job-microphone-0","text":"old loop","source_track":"microphone",
            "start_seconds":0,"end_seconds":20,"superseded_by":["job-microphone-c-0-480000"],"superseded_at_segments_revision":2}]);
        let status=serde_json::json!({"profile":"trelis-20","segments_revision":2,"updated_at":"2026-10-07T12:00:00Z",
            "superseded_segments":archive,"segments":[{"id":"job-microphone-c-0-480000","text":"selected context",
                "source_track":"microphone","replaces_segment_ids":["job-microphone-0"],
                "recognition_original":{"text":"old loop"},"recovery":{"state":"complete","method":"context_window_retry"}}]});
        set_job_metadata_at(root.path(),"meeting-one","source","job",&status).unwrap();
        let loaded=with_job_metadata(&dir,saved.clone()).unwrap();
        assert_eq!(loaded["segments_revision"],2);
        assert_eq!(loaded["superseded_segments"],archive);
        assert_eq!(loaded["segment_metadata"]["job-microphone-c-0-480000"]["original_recognition_text"],"old loop");
        assert_eq!(loaded["segment_metadata"]["job-microphone-c-0-480000"]["recovery"]["method"],"context_window_retry");
        assert_eq!(loaded["corrections"],saved["corrections"]);
        assert_eq!(loaded["notes"],"retained notes");
        assert_eq!(loaded["revision"],1);
        let mut stale=status.clone();stale["segments_revision"]=serde_json::json!(1);stale["superseded_segments"]=serde_json::json!([]);
        set_job_metadata_at(root.path(),"meeting-one","source","job",&stale).unwrap();
        assert_eq!(with_job_metadata(&dir,saved.clone()).unwrap()["superseded_segments"],archive);
        let mut old_progress=status.clone();old_progress["updated_at"]=serde_json::json!("2026-10-07T11:00:00Z");
        old_progress["segments"][0]["recovery"]["state"]=serde_json::json!("waiting_for_context");
        set_job_metadata_at(root.path(),"meeting-one","source","job",&old_progress).unwrap();
        assert_eq!(with_job_metadata(&dir,saved).unwrap()["segment_metadata"]["job-microphone-c-0-480000"]["recovery"]["state"],"complete");
        assert!(set_job_metadata_at(root.path(),"meeting-one","source","another-job",&status).is_err());
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

    #[test]
    fn note_autosave_preserves_corrections_speaker_names_and_progress() {
        let root = tempfile::tempdir().unwrap();
        let project = create_project_at(root.path(), "  Willow  ").unwrap();
        let dir = root.path().join("workspaces").join("meeting-one");
        let mut initial = empty_workspace("meeting-one");
        initial["notes"] = "first note".into();
        initial["corrections"] = serde_json::json!([{"segment_id":"s1","original_text":"बारिश","text":"बारिश है","updated_at":"earlier"}]);
        initial["speaker_names"] = serde_json::json!({"speaker-one":"Maya"});
        initial["profile"] = "trelis-20".into();
        initial["future_field"] = "keep me".into();
        let first = append_revision(&dir,0,initial).unwrap();
        append_revision(&dir.join("job-metadata"),0,serde_json::json!({"source_job_id":"job-one","segment_metadata":{"s1":{"source_track":"system"}},"profile":"trelis-20"})).unwrap();
        let saved = save_meeting_note_at(root.path(),"meeting-one",1,"new note".into(),project["id"].as_str().map(str::to_owned),None).unwrap();
        assert_eq!(saved["revision"],2);
        assert_eq!(saved["corrections"],first["corrections"]);
        assert_eq!(saved["speaker_names"],first["speaker_names"]);
        assert_eq!(saved["future_field"],"keep me");
        assert_eq!(saved["source_job_id"],"job-one");
        assert_eq!(saved["segment_metadata"]["s1"]["source_track"],"system");
        assert_eq!(saved["project_id"],project["id"]);
        assert_eq!(latest_revision(&dir).unwrap().unwrap()["profile"],"trelis-20");
        assert!(save_meeting_note_at(root.path(),"meeting-one",1,"stale note".into(),None,None).is_err());
        assert_eq!(latest_revision(&dir).unwrap().unwrap()["notes"],"new note");
        let unchanged = save_meeting_note_at(root.path(),"meeting-one",2,"new note".into(),project["id"].as_str().map(str::to_owned),None).unwrap();
        assert_eq!(unchanged["revision"],2);
        assert_eq!(serde_json::from_slice::<serde_json::Value>(&fs::read(dir.join("revision-000000000001.json")).unwrap()).unwrap(),first);
    }

    #[test]
    fn note_autosave_rejects_missing_projects_and_keeps_legacy_structure() {
        let root = tempfile::tempdir().unwrap();
        assert!(save_meeting_note_at(root.path(),"meeting-one",0,"draft".into(),Some("missing".into()),None).is_err());
        assert!(!root.path().join("workspaces").exists());
        assert!(save_meeting_note_at(root.path(),"meeting-one",0,"draft".into(),Some("../other".into()),None).is_err());
        let saved = save_meeting_note_at(root.path(),"meeting-one",0,"draft".into(),None,
            Some((Some("legacy".into()),Some("{\"original\":true}".into())))).unwrap();
        assert_eq!(saved["legacy_notes_json"],"{\"original\":true}");
        assert_eq!(saved["notes"],"draft");
        assert!(saved["project_id"].is_null());
    }

    #[test]
    fn project_rename_preserves_evidence_and_rejects_stale_changes() {
        let root = tempfile::tempdir().unwrap();
        let project = create_project_at(root.path(),"  Willow  ").unwrap();
        assert_eq!(project["name"],"Willow");
        let id = project["id"].as_str().unwrap();
        let dir = root.path().join("vaults").join(id);
        let mut value = latest_revision(&dir).unwrap().unwrap();
        value["entries"] = serde_json::json!([{"id":"ref-one","canonical":"Willow","source":"checked note"}]);
        value["relationships"] = serde_json::json!([{"source":"meeting-one"}]);
        let prior = append_revision(&dir,1,value).unwrap();
        let renamed = rename_project_at(root.path(),id,2,"Willow research").unwrap();
        assert_eq!(renamed["entry_count"],1);
        assert_eq!(renamed["revision"],3);
        let after = latest_revision(&dir).unwrap().unwrap();
        assert_eq!(after["entries"],prior["entries"]);
        assert_eq!(after["relationships"],prior["relationships"]);
        assert!(rename_project_at(root.path(),id,2,"stale rename").is_err());
        assert!(create_project_at(root.path()," \n ").is_err());
        assert!(rename_project_at(root.path(),"../escape",0,"bad").is_err());
        assert_eq!(latest_revision(&dir).unwrap().unwrap()["name"],"Willow research");
    }

    #[test]
    fn library_uses_only_saved_project_assignments_and_unicode_safe_previews() {
        let root = tempfile::tempdir().unwrap();
        let legacy = library_meeting(root.path(),"meeting-one".into(),"Old call".into(),"2026-10-01".into(),Some("मेरी\n पुरानी notes".into())).unwrap();
        assert!(legacy["project_id"].is_null());
        assert_eq!(legacy["notes_preview"],"मेरी पुरानी notes");
        assert!(!root.path().join("workspaces").exists());
        let dir = root.path().join("workspaces").join("meeting-two");
        let mut saved = empty_workspace("meeting-two");
        saved["project_id"] = "prior-project".into();
        saved["notes"] = "न".repeat(250).into();
        append_revision(&dir,0,saved).unwrap();
        let row = library_meeting(root.path(),"meeting-two".into(),"Saved call".into(),"2026-10-02".into(),Some("old fallback".into())).unwrap();
        assert_eq!(row["project_id"],"prior-project");
        assert_eq!(row["notes_preview"].as_str().unwrap().chars().count(),180);
        assert!(empty_workspace("new")["project_id"].is_null());
    }
}
