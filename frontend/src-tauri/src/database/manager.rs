use sqlx::{migrate::MigrateDatabase, Result, Sqlite, SqlitePool, Transaction};
use std::fs;
use std::path::Path;
use tauri::Manager;

#[derive(Clone)]
pub struct DatabaseManager {
    pool: SqlitePool,
}

impl DatabaseManager {
    pub async fn new(tauri_db_path: &str, backend_db_path: &str) -> Result<Self> {
        if let Some(parent_dir) = Path::new(tauri_db_path).parent() {
            if !parent_dir.exists() {
                fs::create_dir_all(parent_dir).map_err(|e| sqlx::Error::Io(e))?;
            }
        }

        if !Path::new(tauri_db_path).exists() {
            if Path::new(backend_db_path).exists() {
                log::info!(
                    "Copying database from {} to {}",
                    backend_db_path,
                    tauri_db_path
                );
                Self::snapshot_database(Path::new(backend_db_path),Path::new(tauri_db_path)).await?;
            } else {
                log::info!("Creating database at {}", tauri_db_path);
                Sqlite::create_database(tauri_db_path).await?;
            }
        }

        let pool = SqlitePool::connect(tauri_db_path).await?;

        sqlx::migrate!("./migrations").run(&pool).await?;

        Ok(DatabaseManager { pool })
    }

    async fn snapshot_database(source: &Path, destination: &Path) -> Result<()> {
        static SNAPSHOT_LOCK: tokio::sync::Mutex<()> = tokio::sync::Mutex::const_new(());
        let _guard=SNAPSHOT_LOCK.lock().await;
        if destination.exists() { return Ok(()); }
        let staging=destination.with_file_name(format!("database-migration-{}.sqlite",uuid::Uuid::new_v4()));
        let options=sqlx::sqlite::SqliteConnectOptions::new().filename(source).read_only(true);
        let pool=SqlitePool::connect_with(options).await?;
        let snapshot=sqlx::query("VACUUM INTO ?").bind(staging.to_string_lossy().as_ref()).execute(&pool).await;
        pool.close().await;
        snapshot?;
        if !destination.exists() { fs::rename(&staging,destination).map_err(sqlx::Error::Io)?; }
        Ok(())
    }

    fn local_directory() -> Result<std::path::PathBuf> {
        crate::local_workspace::data_root().map_err(|error|sqlx::Error::Io(std::io::Error::new(std::io::ErrorKind::Other,error)))
    }

    fn legacy_directories(app_handle: &tauri::AppHandle) -> Vec<std::path::PathBuf> {
        let mut directories=Vec::new();
        if let Ok(current)=app_handle.path().app_data_dir() {
            if let Some(parent)=current.parent() { directories.push(parent.join("com.meetily.ai")); }
            directories.push(current);
        }
        directories
    }

    pub async fn new_from_app_handle(app_handle: &tauri::AppHandle) -> Result<Self> {
        let directory=Self::local_directory()?;
        fs::create_dir_all(&directory).map_err(sqlx::Error::Io)?;
        let destination=directory.join("meeting_minutes.sqlite");
        if !destination.exists() {
            // VACUUM INTO takes a consistent SQLite snapshot, including committed
            // WAL data. Never copy a live database file alone or delete its WAL.
            let mut candidates=vec![directory.join("meeting_minutes.db")];
            for old in Self::legacy_directories(app_handle) {
                candidates.push(old.join("meeting_minutes.sqlite")); candidates.push(old.join("meeting_minutes.db"));
            }
            let source=candidates
                .into_iter().find(|path|path.is_file() && path!=&destination);
            if let Some(source)=source {
                Self::snapshot_database(&source,&destination).await?;
                log::info!("Preserved original database; snapshot stored at {}",destination.display());
            }
        }
        Self::new(&destination.to_string_lossy(),&directory.join("meeting_minutes.db").to_string_lossy()).await
    }

    /// Check if this is the first launch (sqlite database doesn't exist yet)
    pub async fn is_first_launch(app_handle: &tauri::AppHandle) -> Result<bool> {
        let app_data_dir = Self::local_directory()?;

        let tauri_db_path = app_data_dir.join("meeting_minutes.sqlite");

        let legacy = Self::legacy_directories(app_handle).into_iter()
            .any(|p|p.join("meeting_minutes.sqlite").is_file() || p.join("meeting_minutes.db").is_file());
        Ok(!tauri_db_path.exists() && !app_data_dir.join("meeting_minutes.db").is_file() && !legacy)
    }

    /// Import a legacy database from the specified path and initialize
    pub async fn import_legacy_database(
        app_handle: &tauri::AppHandle,
        legacy_db_path: &str,
    ) -> Result<Self> {
        let app_data_dir = Self::local_directory()?;

        if !app_data_dir.exists() {
            fs::create_dir_all(&app_data_dir).map_err(|e| sqlx::Error::Io(e))?;
        }

        let target=app_data_dir.join("meeting_minutes.sqlite");
        if target.exists() {
            return Err(sqlx::Error::Protocol("The local database is already initialized. Existing meetings and the selected source were left unchanged.".into()));
        }
        // Snapshot the selected live database directly, including its WAL.
        Self::snapshot_database(Path::new(legacy_db_path),&target).await?;
        Self::new_from_app_handle(app_handle).await
    }

    pub fn pool(&self) -> &SqlitePool {
        &self.pool
    }

    pub async fn with_transaction<T, F, Fut>(&self, f: F) -> Result<T>
    where
        F: FnOnce(&mut Transaction<'_, Sqlite>) -> Fut,
        Fut: std::future::Future<Output = Result<T>>,
    {
        let mut tx = self.pool.begin().await?;
        let result = f(&mut tx).await;

        match result {
            Ok(val) => {
                tx.commit().await?;
                Ok(val)
            }
            Err(err) => {
                tx.rollback().await?;
                Err(err)
            }
        }
    }

    /// Cleanup database connection and checkpoint WAL
    /// This should be called on application shutdown to ensure:
    /// - All WAL changes are written to the main database file
    /// - The .wal and .shm files are deleted
    /// - Connection pool is gracefully closed
    pub async fn cleanup(&self) -> Result<()> {
        log::info!("Starting database cleanup...");

        // Force checkpoint of WAL to main database file and remove WAL file
        // TRUNCATE mode: checkpoints all pages AND deletes the WAL file
        match sqlx::query("PRAGMA wal_checkpoint(TRUNCATE)")
            .execute(&self.pool)
            .await
        {
            Ok(_) => log::info!("WAL checkpoint completed successfully"),
            Err(e) => log::warn!("WAL checkpoint failed (non-fatal): {}", e),
        }

        // Close the connection pool gracefully
        self.pool.close().await;
        log::info!("Database connection pool closed");

        Ok(())
    }
}
