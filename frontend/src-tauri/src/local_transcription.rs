//! Isolated local STT jobs. Recorder health never depends on a model process.
use std::{collections::HashMap, fs, path::{Path, PathBuf}, process::{Child, Command, Stdio}, sync::Mutex};
use once_cell::sync::Lazy;
use serde_json::{json, Value};
use crate::{local_workspace::{data_root, valid_id}, state::AppState};

static CHILDREN: Lazy<Mutex<HashMap<String, Child>>> = Lazy::new(|| Mutex::new(HashMap::new()));
static JOB_START_LOCK: Lazy<Mutex<()>> = Lazy::new(|| Mutex::new(()));

pub fn profile_parts(profile: &str) -> Result<(&'static str, u32), String> {
    match profile {
        "apex-20" => Ok(("apex",20)), "trelis-20" => Ok(("trelis",20)),
        _ => Err("Choose Apex 20s or Trelis 20s".into()),
    }
}

fn read(path: &Path) -> Result<Value, String> {
    // Windows can briefly deny a reader while Python atomically replaces a
    // checkpoint. Retry that sharing conflict; malformed data still fails.
    for attempt in 0..4 {
        match fs::read(path) {
            Ok(bytes)=>return serde_json::from_slice(&bytes).map_err(|e|e.to_string()),
            Err(error) if attempt<3 && (error.kind()==std::io::ErrorKind::PermissionDenied || error.raw_os_error()==Some(32)) => {
                std::thread::sleep(std::time::Duration::from_millis(15));
            }
            Err(error)=>return Err(error.to_string()),
        }
    }
    unreachable!()
}
fn config_path() -> Result<PathBuf, String> { Ok(data_root()?.join("runtime.json")) }
fn job_dir(id: &str) -> Result<PathBuf, String> { valid_id(id)?; Ok(data_root()?.join("jobs").join(id)) }

fn path_key(path: &str) -> String {
    let normalized=Path::new(path).canonicalize().unwrap_or_else(|_|PathBuf::from(path));
    let text=normalized.to_string_lossy().replace('/',"\\");
    #[cfg(target_os="windows")]
    { return text.strip_prefix("\\\\?\\").unwrap_or(&text).to_lowercase(); }
    #[cfg(not(target_os="windows"))]
    { text }
}

#[tauri::command]
pub fn get_local_stt_profiles() -> Result<Vec<Value>, String> {
    let config = config_path().and_then(|p| read(&p)).ok();
    Ok(["trelis-20","apex-20"].iter().map(|id| {
        let (model,seconds) = profile_parts(id).unwrap();
        let accelerated = config.as_ref().map(|c| c["models"][model]["backend"] == "openvino"
            && c["models"][model]["device"] == "GPU"
            && c["models"][model]["export_path"].as_str().map(|p|Path::new(p).join("conversion-provenance.json").is_file()).unwrap_or(false)).unwrap_or(false);
        let available = config.as_ref().map(|c| ["python_executable","worker_script","registry_path"].iter()
            .all(|key| c[key].as_str().map(|p| Path::new(p).is_file()).unwrap_or(false))
            && c["models"][model]["artifact_path"].as_str().map(|p| Path::new(p).exists()).unwrap_or(false)).unwrap_or(false);
        json!({"id":id,"model":model,"chunk_seconds":seconds,"available":available,
            "reason":if !available {"Local runtime needs setup (STTApp/runtime.json)"} else if model=="apex" {"Available as a separate fallback transcript"} else if accelerated {"Local GPU runtime configured; 20-second audio windows plus processing time"} else {"GPU setup is needed for Trelis live transcription; Apex fallback is available"},
            "language_modes":["hinglish","english"],"live_qualified":false,
            "backend":config.as_ref().map(|c|c["models"][model]["backend"].clone()),
            "live_replay_supported":model=="apex" || accelerated,"recommended_timing":if model=="apex" || accelerated {"during-recording"} else {"after-recording"}})
    }).collect())
}

fn capture_session(directory: &str) -> Result<(PathBuf, Value), String> {
    let root = data_root()?.join("recordings").canonicalize().map_err(|e|e.to_string())?;
    let path = PathBuf::from(directory).canonicalize().map_err(|e|e.to_string())?;
    if path.parent() != Some(root.as_path()) || !path.join("timeline.jsonl").is_file() {
        return Err("Choose a local source recording session".into());
    }
    let session = read(&path.join("session.json"))?;
    let id = session["session_id"].as_str().ok_or("Capture session ID is missing")?;
    uuid::Uuid::parse_str(id).map_err(|_|"Invalid capture session ID")?;
    if path.file_name().and_then(|v|v.to_str()) != Some(id) { return Err("Capture directory does not match its session ID".into()); }
    Ok((path,session))
}

#[tauri::command]
pub async fn ensure_capture_meeting(state: tauri::State<'_,AppState>, session_dir: String) -> Result<Value,String> {
    let (path,session) = capture_session(&session_dir)?;
    // Reuse a previous recovery/import of this recording rather than duplicating it.
    let existing: Vec<(String,String,String)> = sqlx::query_as("SELECT id,title,folder_path FROM meetings WHERE folder_path IS NOT NULL ORDER BY created_at,id")
        .fetch_all(state.db_manager.pool()).await.map_err(|e|e.to_string())?;
    let key=path_key(&path.to_string_lossy());
    if let Some((id,title,_))=existing.into_iter().find(|(_,_,folder)|path_key(folder)==key) {
        return Ok(json!({"meeting_id":id,"title":title}));
    }
    let id=format!("meeting-capture-{}",session["session_id"].as_str().unwrap());
    let title=session["meeting_name"].as_str().filter(|s|!s.trim().is_empty()).unwrap_or("Recorded meeting");
    let now=chrono::Utc::now();
    sqlx::query("INSERT OR IGNORE INTO meetings(id,title,created_at,updated_at,folder_path) VALUES(?,?,?,?,?)")
        .bind(&id).bind(title).bind(now).bind(now).bind(path.to_string_lossy().as_ref())
        .execute(state.db_manager.pool()).await.map_err(|e|e.to_string())?;
    Ok(json!({"meeting_id":id,"title":title}))
}

fn launch(id: &str) -> Result<(), String> {
    let dir = job_dir(id)?;
    let snapshot = ensure_job_runtime_snapshot(&dir, &config_path()?)?;
    launch_process(id, &snapshot, &dir)
}

fn validate_asr_runtime(config: &Value) -> Result<(), String> {
    for key in ["python_executable", "worker_script", "registry_path"] {
        let value = config[key].as_str().filter(|value| !value.trim().is_empty())
            .ok_or_else(|| format!("ASR runtime is missing {key}"))?;
        let path = Path::new(value);
        if !path.is_absolute() || !path.is_file() {
            return Err(format!("ASR runtime {key} must refer to an existing absolute file"));
        }
    }
    if !config["models"].as_object().map(|models| !models.is_empty()).unwrap_or(false) {
        return Err("ASR runtime has no model configuration".into());
    }
    Ok(())
}

fn ensure_job_runtime_snapshot(dir: &Path, current_config: &Path) -> Result<PathBuf, String> {
    use std::io::Write;
    let snapshot = dir.join("runtime-snapshot.json");
    match fs::metadata(&snapshot) {
        Ok(_) => {
            // An existing job always keeps its original Python, worker,
            // registry and decoding config, even after the global default changes.
            validate_asr_runtime(&read(&snapshot)?)?;
            return Ok(snapshot);
        }
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
        Err(error) => return Err(error.to_string()),
    }
    match fs::metadata(dir.join("status.json")) {
        Ok(_) => return Err("This existing transcription job has no runtime snapshot. Restore its original runtime snapshot before retrying; the recording and transcript are preserved.".into()),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
        Err(error) => return Err(error.to_string()),
    }
    // The installer migrates older jobs before replacing runtime.json. A
    // checkpoint without a migrated snapshot must never adopt today's engine.
    let bytes = fs::read(current_config).map_err(|e| e.to_string())?;
    let config: Value = serde_json::from_slice(&bytes).map_err(|e| e.to_string())?;
    validate_asr_runtime(&config)?;
    let temporary = dir.join(format!(".runtime-snapshot-{}.tmp", uuid::Uuid::new_v4()));
    let result = (|| -> Result<(), String> {
        let mut file = fs::OpenOptions::new().write(true).create_new(true).open(&temporary).map_err(|e| e.to_string())?;
        file.write_all(&bytes).map_err(|e| e.to_string())?;
        file.sync_all().map_err(|e| e.to_string())?;
        drop(file);
        // Linking a complete sibling publishes atomically without replacing an
        // existing snapshot. Concurrent creators use the winner's exact bytes.
        match fs::hard_link(&temporary, &snapshot) {
            Ok(()) => Ok(()),
            Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => Ok(()),
            Err(error) => Err(format!("Could not publish the job runtime snapshot: {error}")),
        }
    })();
    let _ = fs::remove_file(&temporary);
    result?;
    validate_asr_runtime(&read(&snapshot)?)?;
    Ok(snapshot)
}

fn launch_process(id: &str, config_file: &Path, dir: &Path) -> Result<(),String> {
    let mut children = CHILDREN.lock().map_err(|e| e.to_string())?;
    let mut active = false;
    for child in children.values_mut() { if child.try_wait().map_err(|e| e.to_string())?.is_none() { active = true; } }
    if active { return Err("One local transcription job is already running. Finish or stop it before starting another version.".into()); }
    let config = read(config_file)?;
    let python = config["python_executable"].as_str().ok_or("Missing local Python")?;
    let script = config["worker_script"].as_str().ok_or("Missing local worker")?;
    let log = fs::OpenOptions::new().create(true).append(true).open(dir.join("worker.log")).map_err(|e| e.to_string())?;
    let mut command = Command::new(python);
    command.args(["-B",script,"--config"]).arg(config_file).arg("--request").arg(dir.join("request.json"))
        .stdin(Stdio::null()).stdout(Stdio::from(log.try_clone().map_err(|e| e.to_string())?)).stderr(Stdio::from(log))
        .env("HF_HUB_OFFLINE","1").env("TRANSFORMERS_OFFLINE","1").env("HF_HUB_DISABLE_TELEMETRY","1").env("DO_NOT_TRACK","1")
        .env("STTAPP_PARENT_PID",std::process::id().to_string());
    #[cfg(target_os="windows")]
    { use std::os::windows::process::CommandExt; command.creation_flags(0x08000000); }
    let child = command.spawn().map_err(|e| format!("Local worker could not start: {e}"))?;
    children.insert(id.into(), child);
    Ok(())
}

fn speaker_dir(id: &str) -> Result<PathBuf,String> { valid_id(id)?; Ok(data_root()?.join("speaker-jobs").join(id)) }

#[tauri::command]
pub fn get_speaker_setup_status() -> Result<Value,String> {
    let configured=data_root()?.join("speaker-runtime.json");
    let config=read(&configured).ok();
    let available=config.as_ref().map(|c|["python_executable","worker_script"].iter()
        .all(|key|c[key].as_str().map(|p|Path::new(p).is_file()).unwrap_or(false))
        && c["model_path"].as_str().map(|p|Path::new(p).join("config.yaml").is_file()).unwrap_or(false)
        && c["model_revision"].as_str().map(|s|!s.is_empty()).unwrap_or(false)).unwrap_or(false);
    Ok(json!({"available":available,"model_id":"pyannote/speaker-diarization-community-1",
        "enabled":config.as_ref().map(|c|c["enabled"]!=false).unwrap_or(false),
        "reason":if available {"Local post-call speaker model configured"} else {"Enable Hugging Face model access and install the isolated local speaker runtime"},
        "access_url":"https://huggingface.co/pyannote/speaker-diarization-community-1","live_available":false}))
}

#[tauri::command]
pub async fn start_speaker_identification(state: tauri::State<'_,AppState>, transcription_job_id: String, meeting_id: String) -> Result<Value,String> {
    if get_speaker_setup_status()?["available"]!=true { return Err("The local speaker model needs setup".into()); }
    valid_id(&meeting_id)?;
    let transcript=get_local_transcription_status(transcription_job_id.clone())?;
    if transcript["state"]!="complete" { return Err("Speaker identification starts after final transcription".into()); }
    let folder: Option<String>=sqlx::query_scalar("SELECT folder_path FROM meetings WHERE id=?").bind(&meeting_id)
        .fetch_one(state.db_manager.pool()).await.map_err(|e|e.to_string())?;
    if !folder.as_deref().zip(transcript["session_dir"].as_str()).map(|(a,b)|path_key(a)==path_key(b)).unwrap_or(false) {
        return Err("Speaker job and meeting refer to different recordings".into());
    }
    let id=format!("speakers-{transcription_job_id}");
    let dir=speaker_dir(&id)?;
    if dir.join("request.json").is_file() { return get_speaker_job_status(id); }
    fs::create_dir_all(&dir).map_err(|e|e.to_string())?;
    let mut request=json!({"version":1,"job_id":id,"meeting_id":meeting_id,"session_dir":transcript["session_dir"],
        "transcription_job_id":transcription_job_id,"transcription_status_path":job_dir(&transcription_job_id)?.join("status.json"),
        "created_at":chrono::Utc::now().to_rfc3339()});
    if let Some(previous)=crate::local_workspace::previous_speaker_snapshot(&meeting_id)? {
        let snapshot=dir.join("previous-speakers.json");
        fs::write(&snapshot,serde_json::to_vec_pretty(&previous).map_err(|e|e.to_string())?).map_err(|e|e.to_string())?;
        request["previous_speakers_path"]=json!(snapshot);
    }
    fs::write(dir.join("request.json"),serde_json::to_vec_pretty(&request).map_err(|e|e.to_string())?).map_err(|e|e.to_string())?;
    if let Err(error)=launch_process(&id,&data_root()?.join("speaker-runtime.json"),&dir) {
        // Keep the request for an explicit retry, with an actionable result.
        fs::write(dir.join("status.json"),serde_json::to_vec_pretty(&json!({"job_id":id,"state":"failed","error":error})).map_err(|e|e.to_string())?).map_err(|e|e.to_string())?;
    }
    get_speaker_job_status(id)
}

#[tauri::command]
pub fn get_speaker_job_status(job_id: String) -> Result<Value,String> {
    let dir=speaker_dir(&job_id)?;
    let request=read(&dir.join("request.json"))?;
    let mut status=if dir.join("status.json").is_file() { read(&dir.join("status.json"))? } else { json!({"job_id":job_id,"state":"running"}) };
    let exited={
        let mut children=CHILDREN.lock().map_err(|e|e.to_string())?;
        match children.get_mut(&job_id) { Some(child)=>child.try_wait().map_err(|e|e.to_string())?.is_some(),None=>true }
    };
    if exited && !["complete","failed","stopped"].contains(&status["state"].as_str().unwrap_or("")) {
        status["state"]="failed".into(); status["error"]="Speaker worker stopped. The recording and transcript are preserved; retry when ready.".into();
    }
    project_worker_lifecycle(&mut status, exited);
    if status["state"]=="complete" {
        let meeting=request["meeting_id"].as_str().ok_or("Speaker meeting ID missing")?;
        crate::local_workspace::set_speaker_metadata(meeting,&job_id,&status["result"])?;
    }
    Ok(status)
}

#[tauri::command]
pub fn retry_speaker_identification(job_id: String) -> Result<Value,String> {
    let status=get_speaker_job_status(job_id.clone())?;
    if !["failed","stopped"].contains(&status["state"].as_str().unwrap_or("")) { return Err("Only interrupted speaker jobs can retry".into()); }
    launch_process(&job_id,&data_root()?.join("speaker-runtime.json"),&speaker_dir(&job_id)?)?;
    Ok(json!({"job_id":job_id,"state":"running"}))
}

#[tauri::command]
pub fn start_local_transcription(session_dir: String, profile: String, language_mode: String, project_id: Option<String>, workflow_role: Option<String>) -> Result<Value, String> {
    let _start_guard = JOB_START_LOCK.lock().map_err(|e| e.to_string())?;
    profile_parts(&profile)?;
    validate_workflow_role(workflow_role.as_deref(), &profile)?;
    if !["hinglish","english"].contains(&language_mode.as_str()) { return Err("Choose a declared Hinglish or English decoder profile".into()); }
    if let Some(id) = &project_id { valid_id(id)?; }
    let (session,capture) = capture_session(&session_dir)?;
    // A duplicate stop/start event or reloaded UI must attach to the original
    // pass, not create another model output or overwrite its corrections.
    if let Some(role) = workflow_role.as_deref() {
        if let Some(existing) = find_workflow_job(&session.to_string_lossy(), role)? {
            return get_local_transcription_status(existing);
        }
        if role == "final" && !capture_ready_for_final(&session)? {
            return Err("Finalize or recover the saved recording before starting its final transcript".into());
        }
        if role == "live-final" {
            let config = read(&config_path()?)?;
            if config["models"]["trelis"]["backend"] != "openvino" || config["models"]["trelis"]["device"] != "GPU" {
                return Err("Trelis live transcription needs the local GPU runtime. Recording is preserved; use Apex fallback or finish GPU setup.".into());
            }
        }
    }
    let job_id = uuid::Uuid::new_v4().to_string();
    let dir = job_dir(&job_id)?;
    fs::create_dir_all(&dir).map_err(|e| e.to_string())?;
    let request = json!({"version":1,"job_id":job_id,"session_dir":session.to_string_lossy(),"profile":profile,
        "language_mode":language_mode,"project_id":project_id,"workflow_role":workflow_role,
        "capture_session_id":capture["session_id"],"created_at":chrono::Utc::now().to_rfc3339()});
    fs::write(dir.join("request.json"),serde_json::to_vec_pretty(&request).map_err(|e| e.to_string())?).map_err(|e| e.to_string())?;
    launch(&job_id)?;
    get_local_transcription_status(job_id)
}

fn capture_ready_for_final(session: &Path) -> Result<bool, String> {
    use sha2::{Digest, Sha256};
    let bytes = fs::read(session.join("timeline.jsonl")).map_err(|e| e.to_string())?;
    let committed = bytes.iter().rposition(|byte| *byte == b'\n').map(|last| &bytes[..=last]).unwrap_or(&[]);
    let records = committed.split(|byte| *byte == b'\n').filter_map(|line| serde_json::from_slice::<Value>(line).ok()).collect::<Vec<_>>();
    if records.iter().any(|entry| entry["kind"] == "capture_finalized") { return Ok(true); }
    // Recovery is a verified completion path too; a stale marker cannot finalize
    // audio whose capture journal has subsequently changed.
    let recovered = read(&session.join("recovery-verified.json")).unwrap_or(Value::Null);
    let metadata = read(&session.join("metadata.json")).unwrap_or(Value::Null);
    let final_time = records.iter().filter_map(|entry| match entry["kind"].as_str() {
        Some("audio_chunk") => entry["end_seconds"].as_f64(),
        Some("capture_stopped") => entry["at_seconds"].as_f64(),
        _ => None,
    }).fold(0.0f64, f64::max);
    let valid_duration = recovered["duration_seconds"].as_f64().map(|duration| duration.is_finite() && duration >= final_time).unwrap_or(false);
    Ok(metadata["status"] == "recovered" && valid_duration && recovered["verified"] == true
        && recovered["source_journal_sha256"] == format!("{:x}", Sha256::digest(&bytes)))
}

fn validate_workflow_role(role: Option<&str>, profile: &str) -> Result<(), String> {
    match role {
        None => Ok(()),
        Some("live-draft") if profile == "apex-20" => Ok(()),
        Some("final") if profile == "trelis-20" => Ok(()),
        Some("live-final") if profile == "trelis-20" => Ok(()),
        Some("fallback") if profile == "apex-20" => Ok(()),
        _ => Err("Use Trelis 20s for the live transcript or Apex 20s for its fallback".into()),
    }
}

fn find_workflow_job(session: &str, role: &str) -> Result<Option<String>, String> {
    let root = data_root()?.join("jobs");
    if !root.is_dir() { return Ok(None); }
    let mut matches = Vec::new();
    for entry in fs::read_dir(root).map_err(|e| e.to_string())? {
        let path = entry.map_err(|e| e.to_string())?.path();
        if !path.join("request.json").is_file() { continue; }
        let request = match read(&path.join("request.json")) {
            Ok(request) => request,
            Err(error) => { log::warn!("Ignoring unreadable old job request at {}: {error}", path.display()); continue; }
        };
        if request["workflow_role"] == role && request["session_dir"].as_str().map(|p| path_key(p) == path_key(session)).unwrap_or(false) {
            if let Some(id) = path.file_name().and_then(|v| v.to_str()) {
                valid_id(id)?;
                matches.push((request["created_at"].as_str().unwrap_or("").to_owned(), id.to_owned()));
            }
        }
    }
    matches.sort();
    Ok(matches.into_iter().next().map(|(_, id)| id))
}

#[tauri::command]
pub fn get_local_transcription_status(job_id: String) -> Result<Value, String> {
    let dir = job_dir(&job_id)?;
    let request = read(&dir.join("request.json"))?;
    let mut result = if dir.join("status.json").exists() { read(&dir.join("status.json"))? } else {
        json!({"job_id":job_id,"session_dir":request["session_dir"],"profile":request["profile"],"state":"running",
            "processed_audio_seconds":0,"available_audio_seconds":0,"backlog_seconds":0,"segments":[]})
    };
    let mut children = CHILDREN.lock().map_err(|e| e.to_string())?;
    let exited = match children.get_mut(&job_id) {
        Some(child) => child.try_wait().map_err(|e| e.to_string())?.is_some(),
        None => true,
    };
    if exited && ["running","waiting_for_audio"].contains(&result["state"].as_str().unwrap_or("")) {
        result["state"] = "failed".into();
        result["error"] = "Worker exited or the application restarted. Saved audio and completed results remain available; resume the job.".into();
    }
    project_worker_lifecycle(&mut result, exited);
    result["workflow_role"] = request["workflow_role"].clone();
    result["capture_session_id"] = request["capture_session_id"].clone();
    result["language_mode"] = request["language_mode"].clone();
    result["created_at"] = request["created_at"].clone();
    Ok(result)
}

fn project_worker_lifecycle(status: &mut Value, exited: bool) {
    // A durable terminal checkpoint precedes Python/model teardown. Keep the
    // queue occupied until that process releases its resources; otherwise the
    // next transcription or automatic speaker job can fail during this gap.
    if !exited && ["complete", "failed", "stopped"].contains(&status["state"].as_str().unwrap_or("")) {
        status["checkpoint_state"] = status["state"].clone();
        status["state"] = "running".into();
    }
}

#[tauri::command]
pub fn stop_local_transcription(job_id: String) -> Result<Value, String> {
    fs::write(job_dir(&job_id)?.join("stop.request"),b"stop after current inference").map_err(|e| e.to_string())?;
    Ok(json!({"job_id":job_id,"state":"stop_requested"}))
}

#[tauri::command]
pub fn resume_local_transcription(job_id: String) -> Result<Value, String> {
    let status = get_local_transcription_status(job_id.clone())?;
    if !["failed","stopped"].contains(&status["state"].as_str().unwrap_or("")) { return Err("Only failed or stopped jobs can resume".into()); }
    let stop = job_dir(&job_id)?.join("stop.request");
    if stop.is_file() { fs::remove_file(stop).map_err(|e| e.to_string())?; }
    launch(&job_id)?;
    Ok(json!({"job_id":job_id,"state":"running"}))
}

#[tauri::command]
pub fn list_local_transcription_jobs(session_dir: Option<String>) -> Result<Vec<Value>,String> {
    let root = data_root()?.join("jobs");
    if !root.exists() { return Ok(vec![]); }
    let mut jobs = Vec::new();
    for item in fs::read_dir(root).map_err(|e| e.to_string())? {
        let path = item.map_err(|e| e.to_string())?.path();
        if let Some(id) = path.file_name().and_then(|p| p.to_str()) {
            if path.join("request.json").is_file() {
                let status = get_local_transcription_status(id.into())?;
                if session_dir.as_ref().map(|s| status["session_dir"].as_str().map(|p|path_key(p)==path_key(s)).unwrap_or(false)).unwrap_or(true) { jobs.push(status); }
            }
        }
    }
    Ok(jobs)
}

async fn has_transcript_bindings(state: &tauri::State<'_, AppState>) -> Result<bool, String> {
    let count: i64 = sqlx::query_scalar("SELECT count(*) FROM sqlite_master WHERE type='table' AND name='local_transcription_bindings'")
        .fetch_one(state.db_manager.pool()).await.map_err(|e| e.to_string())?;
    Ok(count > 0)
}

/// Durable navigation metadata. Automatic draft and fallback children are grouped out
/// of the sidebar; existing independent/experimental transcripts remain visible.
#[tauri::command]
pub async fn get_local_transcript_groups(state: tauri::State<'_, AppState>) -> Result<Vec<Value>, String> {
    if !has_transcript_bindings(&state).await? { return Ok(vec![]); }
    let bindings: Vec<(String,String,String)> = sqlx::query_as("SELECT job_id,meeting_id,source_meeting_id FROM local_transcription_bindings")
        .fetch_all(state.db_manager.pool()).await.map_err(|e| e.to_string())?;
    let mut groups = Vec::new();
    for (job, meeting, source) in bindings {
        if meeting == source { continue; }
        let request = job_dir(&job).and_then(|dir| read(&dir.join("request.json"))).unwrap_or(Value::Null);
        if request["workflow_role"] == "live-draft" || request["workflow_role"] == "fallback" {
            groups.push(json!({"meeting_id":meeting,"source_meeting_id":source,"workflow_role":request["workflow_role"]}));
        }
    }
    Ok(groups)
}

#[tauri::command]
pub async fn get_local_transcript_layers(state: tauri::State<'_, AppState>, meeting_id: String) -> Result<Value, String> {
    valid_id(&meeting_id)?;
    let exists: i64 = sqlx::query_scalar("SELECT count(*) FROM meetings WHERE id=?").bind(&meeting_id)
        .fetch_one(state.db_manager.pool()).await.map_err(|e| e.to_string())?;
    if exists == 0 { return Err("Conversation not found".into()); }
    let bindings = has_transcript_bindings(&state).await?;
    let mut source = meeting_id.clone();
    if bindings {
        let mut visited = std::collections::HashSet::new();
        while visited.insert(source.clone()) {
            let parent: Option<String> = sqlx::query_scalar("SELECT source_meeting_id FROM local_transcription_bindings WHERE meeting_id=?")
                .bind(&source).fetch_optional(state.db_manager.pool()).await.map_err(|e| e.to_string())?;
            match parent { Some(id) if id != source => source = id, _ => break }
        }
    }
    let rows: Vec<(String,String,Option<String>)> = if bindings {
        sqlx::query_as("WITH RECURSIVE related(id) AS (SELECT ? UNION SELECT b.meeting_id FROM local_transcription_bindings b JOIN related r ON b.source_meeting_id=r.id) SELECT m.id,m.title,b.job_id FROM meetings m JOIN related r ON m.id=r.id LEFT JOIN local_transcription_bindings b ON b.meeting_id=m.id ORDER BY m.created_at,m.id")
            .bind(&source).fetch_all(state.db_manager.pool()).await.map_err(|e| e.to_string())?
    } else {
        sqlx::query_as("SELECT id,title,NULL FROM meetings WHERE id=?")
            .bind(&source).fetch_all(state.db_manager.pool()).await.map_err(|e| e.to_string())?
    };
    let layers = rows.into_iter().map(|(id,title,job)| {
        let status = job.as_ref().and_then(|job| get_local_transcription_status(job.clone()).ok()).unwrap_or(Value::Null);
        json!({"meeting_id":id,"title":title,"profile":status["profile"],"workflow_role":status["workflow_role"],
            "job_id":job,"state":status["state"].as_str().unwrap_or(if job.is_some() { "unavailable" } else { "not_started" }),"primary":id == source})
    }).collect::<Vec<_>>();
    Ok(json!({"source_meeting_id":source,"layers":layers}))
}

#[tauri::command]
pub async fn import_local_transcription(state: tauri::State<'_,AppState>, job_id: String, meeting_id: String, primary: Option<bool>) -> Result<Value,String> {
    let status = get_local_transcription_status(job_id.clone())?;
    let title: String = sqlx::query_scalar("SELECT title FROM meetings WHERE id = ?").bind(&meeting_id)
        .fetch_one(state.db_manager.pool()).await.map_err(|e| e.to_string())?;
    let folder: Option<String> = sqlx::query_scalar("SELECT folder_path FROM meetings WHERE id = ?").bind(&meeting_id)
        .fetch_one(state.db_manager.pool()).await.map_err(|e|e.to_string())?;
    if !folder.as_deref().zip(status["session_dir"].as_str()).map(|(a,b)|path_key(a)==path_key(b)).unwrap_or(false) {
        return Err("The transcription job belongs to a different recording".into());
    }
    let profile = status["profile"].as_str().ok_or("Job profile missing")?;
    let mut tx = state.db_manager.pool().begin().await.map_err(|e| e.to_string())?;
    sqlx::query("CREATE TABLE IF NOT EXISTS local_transcription_bindings (job_id TEXT PRIMARY KEY, meeting_id TEXT NOT NULL UNIQUE REFERENCES meetings(id) ON DELETE CASCADE, source_meeting_id TEXT NOT NULL REFERENCES meetings(id) ON DELETE CASCADE)")
        .execute(&mut *tx).await.map_err(|e|e.to_string())?;
    let binding: Option<(String,String)> = sqlx::query_as("SELECT meeting_id,source_meeting_id FROM local_transcription_bindings WHERE job_id=?")
        .bind(&job_id).fetch_optional(&mut *tx).await.map_err(|e|e.to_string())?;
    let source_meeting_id=binding.as_ref().map(|(_,source)|source.clone()).unwrap_or_else(||meeting_id.clone());
    let child_id = if let Some((bound,_))=binding { bound } else if primary.unwrap_or(false) {
        let occupied: i64 = sqlx::query_scalar("SELECT (SELECT count(*) FROM transcripts WHERE meeting_id=?) + (SELECT count(*) FROM local_transcription_bindings WHERE meeting_id=?)")
            .bind(&meeting_id).bind(&meeting_id).fetch_one(&mut *tx).await.map_err(|e|e.to_string())?;
        if occupied>0 { return Err("This meeting already has a transcript. Import the new version as a separate comparison.".into()); }
        meeting_id.clone()
    } else { format!("meeting-local-{job_id}") };
    let now = chrono::Utc::now();
    sqlx::query("INSERT OR IGNORE INTO meetings(id,title,created_at,updated_at,folder_path) VALUES(?,?,?,?,?)")
        .bind(&child_id).bind(format!("{title} — {}", match status["workflow_role"].as_str() { Some("live-draft") => "Apex live draft", Some("fallback") => "Apex fallback", _ => profile })).bind(now).bind(now).bind(status["session_dir"].as_str())
        .execute(&mut *tx).await.map_err(|e| e.to_string())?;
    sqlx::query("INSERT OR IGNORE INTO local_transcription_bindings(job_id,meeting_id,source_meeting_id) VALUES(?,?,?)")
        .bind(&job_id).bind(&child_id).bind(&source_meeting_id).execute(&mut *tx).await.map_err(|e|e.to_string())?;
    let mut imported = 0;
    for segment in status["segments"].as_array().ok_or("Worker segments missing")? {
        let text = segment["text"].as_str().ok_or("Invalid raw transcript")?;
        if text.is_empty() { continue; }
        let start = segment["start_seconds"].as_f64().ok_or("Missing window time")?;
        let end = segment["end_seconds"].as_f64().ok_or("Missing window time")?;
        if !start.is_finite() || !end.is_finite() || end<=start { return Err("Invalid window times".into()); }
        let done = sqlx::query("INSERT OR IGNORE INTO transcripts(id,meeting_id,transcript,timestamp,audio_start_time,audio_end_time,duration) VALUES(?,?,?,?,?,?,?)")
            .bind(segment["id"].as_str().ok_or("Missing segment ID")?).bind(&child_id).bind(text)
            .bind(format!("{:02}:{:02}",start as u64/60,start as u64%60)).bind(start).bind(end).bind(end-start)
            .execute(&mut *tx).await.map_err(|e| e.to_string())?;
        imported += done.rows_affected();
    }
    tx.commit().await.map_err(|e| e.to_string())?;
    crate::local_workspace::set_job_metadata(&child_id,&source_meeting_id,&job_id,&status)?;
    Ok(json!({"meeting_id":child_id,"imported_count":imported}))
}

fn claude_link(mode: &str, file: Option<&Path>) -> Result<String,String> {
    let mut url = url::Url::parse(match mode { "chat"=>"claude://claude.ai/new", "cowork"=>"claude://cowork/new", _=>return Err("Choose chat or cowork".into()) }).map_err(|e| e.to_string())?;
    if mode=="cowork" {
        url.query_pairs_mut().append_pair("q","Please review the attached meeting transcript. Prepare a refined English summary with decisions and action items, citing transcript times. Preserve uncertainty; do not invent owners, dates or quantities.")
            .append_pair("file",&file.ok_or("Missing transcript attachment")?.to_string_lossy());
    }
    Ok(url.into())
}

#[tauri::command]
pub fn open_claude_desktop(mode: String, transcript: String, title: String) -> Result<Value,String> {
    if transcript.trim().is_empty() || transcript.len()>20_000_000 { return Err("Transcript is empty or too large for this handoff".into()); }
    let attachment = if mode=="cowork" {
        let root=data_root()?.join("handoffs"); fs::create_dir_all(&root).map_err(|e|e.to_string())?;
        let path=root.join(format!("transcript-{}.txt",uuid::Uuid::new_v4()));
        fs::write(&path,format!("{title}\n\n{transcript}")).map_err(|e|e.to_string())?; Some(path)
    } else { None };
    let link=claude_link(&mode,attachment.as_deref())?;
    #[cfg(target_os="windows")]
    {
        use std::os::windows::ffi::OsStrExt;
        #[link(name="shell32")]
        extern "system" { fn ShellExecuteW(hwnd: *mut std::ffi::c_void, operation:*const u16,file:*const u16,params:*const u16,dir:*const u16,show:i32)->isize; }
        let encoded:Vec<u16>=std::ffi::OsStr::new(&link).encode_wide().chain(Some(0)).collect();
        let opened=unsafe { ShellExecuteW(std::ptr::null_mut(),std::ptr::null(),encoded.as_ptr(),std::ptr::null(),std::ptr::null(),1) };
        if opened<=32 { return Err("Windows could not open Claude Desktop. Install/open Claude, then paste the copied transcript or attach the exported TXT.".into()); }
    }
    #[cfg(not(target_os="windows"))]
    { let _=link; return Err("This app handoff currently supports Windows; use Copy for Claude or TXT export.".into()); }
    #[allow(unreachable_code)]
    Ok(json!({"opened":true,"requires_paste":mode=="chat","path":attachment.map(|p|p.to_string_lossy().to_string())}))
}

#[cfg(test)]
mod tests {
    use super::*;
    fn synthetic_asr_runtime(root: &Path, label: &str) -> Value {
        let mut config = json!({"models":{"trelis":{"backend":label}}});
        for key in ["python_executable", "worker_script", "registry_path"] {
            let path = root.join(format!("{label}-{key}"));
            fs::write(&path, b"synthetic fixture; never executed").unwrap();
            config[key] = json!(path);
        }
        config
    }
    #[test] fn asr_runtime_snapshot_keeps_old_runtime_after_global_change() {
        let root = tempfile::tempdir().unwrap();
        let job = root.path().join("job"); fs::create_dir(&job).unwrap();
        let current = root.path().join("runtime.json");
        let old = synthetic_asr_runtime(root.path(), "old-engine");
        let bytes = serde_json::to_vec_pretty(&old).unwrap();
        fs::write(&current, &bytes).unwrap();
        let snapshot = ensure_job_runtime_snapshot(&job, &current).unwrap();
        assert_eq!(fs::read(&snapshot).unwrap(), bytes);
        fs::write(job.join("status.json"), b"{}").unwrap();
        let replacement = synthetic_asr_runtime(root.path(), "new-engine");
        fs::write(&current, serde_json::to_vec(&replacement).unwrap()).unwrap();
        assert_eq!(ensure_job_runtime_snapshot(&job, &current).unwrap(), snapshot);
        assert_eq!(read(&snapshot).unwrap(), old);
        fs::remove_file(&current).unwrap();
        assert_eq!(ensure_job_runtime_snapshot(&job, &current).unwrap(), snapshot);
        assert_eq!(fs::read(&snapshot).unwrap(), bytes);
    }
    #[test] fn asr_runtime_snapshot_rejects_invalid_or_missing_files_without_replacement() {
        let root = tempfile::tempdir().unwrap();
        let job = root.path().join("job"); fs::create_dir(&job).unwrap();
        let current = root.path().join("runtime.json");
        let snapshot = job.join("runtime-snapshot.json");
        assert!(ensure_job_runtime_snapshot(&job, &current).is_err());
        fs::write(&current, b"{broken").unwrap();
        assert!(ensure_job_runtime_snapshot(&job, &current).is_err());
        assert!(!snapshot.exists());
        let config = synthetic_asr_runtime(root.path(), "engine");
        fs::write(&current, serde_json::to_vec(&config).unwrap()).unwrap();
        fs::write(&snapshot, b"{broken").unwrap();
        assert!(ensure_job_runtime_snapshot(&job, &current).is_err());
        assert_eq!(fs::read(&snapshot).unwrap(), b"{broken");
        fs::write(&snapshot, serde_json::to_vec(&config).unwrap()).unwrap();
        for key in ["python_executable", "worker_script", "registry_path"] {
            let path = Path::new(config[key].as_str().unwrap());
            fs::remove_file(path).unwrap();
            assert!(ensure_job_runtime_snapshot(&job, &current).is_err());
            assert_eq!(read(&snapshot).unwrap(), config);
            fs::write(path, b"synthetic fixture").unwrap();
        }
        let mut relative = config.clone(); relative["worker_script"] = json!("worker.py");
        assert!(validate_asr_runtime(&relative).is_err());
    }
    #[test] fn legacy_asr_checkpoint_requires_original_runtime_migration() {
        let root = tempfile::tempdir().unwrap();
        let job = root.path().join("job"); fs::create_dir(&job).unwrap();
        let current = root.path().join("runtime.json");
        fs::write(&current, serde_json::to_vec(&synthetic_asr_runtime(root.path(), "new")).unwrap()).unwrap();
        let status = b"{\"state\":\"stopped\",\"segments\":[{\"text\":\"preserved\"}]}";
        fs::write(job.join("status.json"), status).unwrap();
        assert!(ensure_job_runtime_snapshot(&job, &current).unwrap_err().contains("original runtime"));
        assert!(!job.join("runtime-snapshot.json").exists());
        let old = synthetic_asr_runtime(root.path(), "original");
        fs::write(job.join("runtime-snapshot.json"), serde_json::to_vec(&old).unwrap()).unwrap();
        let chosen = ensure_job_runtime_snapshot(&job, &current).unwrap();
        assert_eq!(read(&chosen).unwrap(), old);
        assert_eq!(fs::read(job.join("status.json")).unwrap(), status);
    }
    #[test] fn concurrent_asr_snapshot_creation_has_one_complete_winner() {
        let root = tempfile::tempdir().unwrap();
        let job = root.path().join("job"); fs::create_dir(&job).unwrap();
        let first = root.path().join("first.json");
        let second = root.path().join("second.json");
        let first_bytes = serde_json::to_vec(&synthetic_asr_runtime(root.path(), "first")).unwrap();
        let second_bytes = serde_json::to_vec(&synthetic_asr_runtime(root.path(), "second")).unwrap();
        fs::write(&first, &first_bytes).unwrap(); fs::write(&second, &second_bytes).unwrap();
        let barrier = std::sync::Arc::new(std::sync::Barrier::new(2));
        let handles: Vec<_> = [first, second].into_iter().map(|config| {
            let dir = job.clone(); let barrier = barrier.clone();
            std::thread::spawn(move || { barrier.wait(); ensure_job_runtime_snapshot(&dir, &config) })
        }).collect();
        for handle in handles { assert!(handle.join().unwrap().is_ok()); }
        let actual = fs::read(job.join("runtime-snapshot.json")).unwrap();
        assert!(actual == first_bytes || actual == second_bytes);
        assert_eq!(fs::read_dir(&job).unwrap().count(), 1);
    }
    #[test] fn automatic_roles_do_not_admit_swapped_models() {
        assert!(validate_workflow_role(Some("live-draft"), "apex-20").is_ok());
        assert!(validate_workflow_role(Some("final"), "trelis-20").is_ok());
        assert!(validate_workflow_role(Some("live-final"), "trelis-20").is_ok());
        assert!(validate_workflow_role(Some("fallback"), "apex-20").is_ok());
        assert!(validate_workflow_role(Some("live-final"), "apex-20").is_err());
        assert!(validate_workflow_role(Some("fallback"), "trelis-20").is_err());
        assert!(validate_workflow_role(Some("final"), "apex-20").is_err());
        assert!(validate_workflow_role(Some("live-draft"), "trelis-20").is_err());
        assert!(validate_workflow_role(None, "apex-20").is_ok());
    }
    #[test] fn final_requires_committed_completion_or_matching_recovery() {
        use sha2::{Digest, Sha256};
        let root = tempfile::tempdir().unwrap();
        let timeline = root.path().join("timeline.jsonl");
        fs::write(&timeline, b"{\"kind\":\"capture_finalized\"}").unwrap();
        assert!(!capture_ready_for_final(root.path()).unwrap());
        fs::write(&timeline, b"{\"kind\":\"capture_finalized\"}\n").unwrap();
        assert!(capture_ready_for_final(root.path()).unwrap());
        let interrupted = b"{\"kind\":\"capture_started\"}\n";
        fs::write(&timeline, interrupted).unwrap();
        fs::write(root.path().join("metadata.json"), b"{\"status\":\"recovered\"}").unwrap();
        fs::write(root.path().join("recovery-verified.json"), serde_json::to_vec(&json!({"verified":true,"duration_seconds":1.0,"source_journal_sha256":format!("{:x}",Sha256::digest(interrupted))})).unwrap()).unwrap();
        assert!(capture_ready_for_final(root.path()).unwrap());
        fs::write(root.path().join("metadata.json"), b"{\"status\":\"recording\"}").unwrap();
        assert!(!capture_ready_for_final(root.path()).unwrap());
        fs::write(&timeline, b"changed\n").unwrap();
        assert!(!capture_ready_for_final(root.path()).unwrap());
    }
    #[test] fn only_requested_profiles_are_admitted() {
        for retired in ["trelis-15","trelis-30","apex-15","apex-30"] { assert!(profile_parts(retired).is_err()); }
        assert_eq!(profile_parts("trelis-20").unwrap(),("trelis",20));
        assert_eq!(profile_parts("apex-20").unwrap(),("apex",20));
    }
    #[test] fn claude_chat_never_truncates_transcript_into_uri() {
        assert_eq!(claude_link("chat",None).unwrap(),"claude://claude.ai/new");
        let uri=claude_link("cowork",Some(Path::new("C:/private/a & b.txt"))).unwrap();
        let url=url::Url::parse(&uri).unwrap(); assert_eq!(url.query_pairs().find(|(k,_)| k=="file").unwrap().1,"C:/private/a & b.txt");
        assert!(claude_link("unsupported",None).is_err());
    }
}
