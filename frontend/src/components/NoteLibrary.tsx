'use client';
import { useEffect, useMemo, useState } from 'react';
import { invoke } from '@tauri-apps/api/core';
import { useRouter } from 'next/navigation';
import { ArrowUpRight, BookOpen, ChevronRight, FileText, Folder, Plus, Search, X } from 'lucide-react';
import { toast } from 'sonner';
import { Dialog, DialogContent, DialogTitle, DialogDescription } from '@/components/ui/dialog';
import { useProjectLibrary, projectLabel, type Project } from '@/hooks/useProjectLibrary';

export function NoteLibrary({ onNewNote, busy, recovery }: { onNewNote: (projectId: string | null) => void; busy: boolean; recovery?: React.ReactNode }) {
  const library = useProjectLibrary();
  const router = useRouter();
  const [selected, setSelected] = useState('all');
  const [query, setQuery] = useState('');
  const [memory, setMemory] = useState(false);
  const [projectDialog, setProjectDialog] = useState<'new' | Project | null>(null);
  const [name, setName] = useState('');
  const [saving, setSaving] = useState(false);
  const [references, setReferences] = useState<{ canonical: string; source: string; verified: boolean; context: string }[]>([]);
  const [referenceError, setReferenceError] = useState<string | null>(null);
  const project = library.projects.find(item => item.id === selected);
  const notes = useMemo(() => library.meetings.filter(note => (selected === 'all' || (note.project_id ?? 'unfiled') === selected) && `${note.title} ${note.notes_preview}`.toLocaleLowerCase().includes(query.toLocaleLowerCase())), [library.meetings, selected, query]);
  useEffect(() => {
    const pending = sessionStorage.getItem('xx.open-project');
    if (pending) { setSelected(pending); sessionStorage.removeItem('xx.open-project'); }
    const select = (event: Event) => { setSelected((event as CustomEvent<string>).detail); setMemory(false); };
    window.addEventListener('xx-select-project', select);
    return () => window.removeEventListener('xx-select-project', select);
  }, []);
  useEffect(() => {
    if (!project || !memory) return;
    let alive = true; setReferences([]); setReferenceError(null);
    void invoke<{ entries: typeof references }>('load_project_vault', { projectId: project.id }).then(value => { if (alive) setReferences(value.entries); }).catch(reason => { if (alive) setReferenceError(String(reason)); });
    return () => { alive = false; };
  }, [project?.id, memory]);
  const open = (id: string) => router.push(`/meeting-details?id=${encodeURIComponent(id)}`);
  const saveProject = async () => {
    if (!name.trim() || saving) return;
    setSaving(true);
    try {
      const saved = await invoke<Project>(projectDialog === 'new' ? 'create_project' : 'rename_project', projectDialog === 'new' ? { name: name.trim() } : { projectId: (projectDialog as Project).id, expectedRevision: (projectDialog as Project).revision, name: name.trim() });
      await library.refresh(); setSelected(saved.id); setProjectDialog(null); window.dispatchEvent(new Event('xx-projects-updated'));
    } catch (reason) { toast.error('Project could not be saved', { description: String(reason) }); }
    finally { setSaving(false); }
  };
  const missingProjects = [...new Set(notes.map(note => note.project_id).filter((id): id is string => !!id && !library.projects.some(project => project.id === id)))];
  const groups = selected === 'all' ? [...library.projects.map(item => ({ id: item.id, name: projectLabel(item.name) })), ...missingProjects.map(id => ({ id, name: 'Unavailable project' })), { id: 'unfiled', name: 'Unfiled' }].filter(group => notes.some(note => (note.project_id ?? 'unfiled') === group.id)) : [{ id: selected, name: project ? projectLabel(project.name) : 'Unfiled' }];
  return <div className="xx-library">
    <header className="xx-library-header"><div><p className="xx-library-kicker">A little space to think</p><h1>{project ? projectLabel(project.name) : selected === 'unfiled' ? 'Unfiled notes' : 'Your notes'}</h1></div><button className="xx-button-primary" title="Create a note and start recording microphone and computer audio" disabled={busy} onClick={() => onNewNote(project?.id ?? null)}><Plus size={16} />New note</button></header>
    <div className="xx-library-toolbar"><label className="xx-library-search"><Search size={16} /><input aria-label="Search notes" placeholder="Find a note…" value={query} onChange={event => setQuery(event.target.value)} />{query && <button aria-label="Clear note search" onClick={() => setQuery('')}><X size={14} /></button>}</label><button className="xx-icon-button" title="New project" aria-label="New project" onClick={() => { setName(''); setProjectDialog('new'); }}><Folder size={17} /><Plus size={10} /></button></div>
    <nav className="xx-project-tabs" aria-label="Projects"><button aria-pressed={selected === 'all'} onClick={() => { setSelected('all'); setMemory(false); }}>All notes</button>{library.projects.map(item => <button key={item.id} aria-pressed={selected === item.id} onClick={() => { setSelected(item.id); setMemory(false); }}><Folder size={13} />{projectLabel(item.name)}</button>)}<button aria-pressed={selected === 'unfiled'} onClick={() => { setSelected('unfiled'); setMemory(false); }}>Unfiled</button></nav>
    {recovery}
    {project && <div className="xx-project-actions"><button className="xx-memory-link" onClick={() => setMemory(!memory)} aria-expanded={memory}><BookOpen size={15} />{memory ? 'Back to notes' : 'Project memory'}<ChevronRight size={13} /></button><button className="text-xs text-[var(--xx-muted)]" onClick={() => { setName(projectLabel(project.name)); setProjectDialog(project); }}>Rename project</button></div>}
    {library.error && <div role="alert" className="xx-inline-error">Your notes could not be loaded. <button onClick={() => void library.refresh()}>Try again</button></div>}
    <div className="xx-library-list custom-scrollbar">
      {memory && project ? <section aria-label="Project memory" className="xx-project-memory"><h2>A thread through your conversations</h2><p className="xx-memory-intro">Revisit what you wrote and the references you kept for {projectLabel(project.name)}. Each note opens its original recording and transcript.</p>
        <h3>From your notes</h3>{notes.filter(note => note.notes_preview).map(note => <button key={note.id} className="xx-memory-note" onClick={() => open(note.id)}><strong>{note.title}<ArrowUpRight size={14} /></strong><p>{note.notes_preview}</p></button>)}{!notes.some(note => note.notes_preview) && <p className="xx-empty-hint">Notes from your project meetings will collect here as you write.</p>}
        <h3>Project references</h3>{referenceError && <p role="alert">References could not be loaded.</p>}{references.map((item, index) => <article key={index} className="xx-reference"><strong>{item.canonical}</strong><span>{item.verified ? 'Checked reference' : 'Unverified reference'}</span><p>{item.context}</p>{item.source && <p className="text-xs">Source: {item.source}</p>}</article>)}{!references.length && !referenceError && <p className="xx-empty-hint">No references yet. You can keep a checked reference in a note’s project vault.</p>}
        <p className="xx-memory-footnote">A source shelf, built from your saved notes. No generated claims or automatic name corrections.</p>
      </section> : <>{groups.map(group => <section className="xx-library-group" key={group.id}><h2><Folder size={14} />{group.name}<span>{notes.filter(note => (note.project_id ?? 'unfiled') === group.id).length}</span></h2>{notes.filter(note => (note.project_id ?? 'unfiled') === group.id).map(note => <button key={note.id} className="xx-note-row" onClick={() => open(note.id)}><FileText size={17} /><span><strong>{note.title}</strong><small>{note.notes_preview || 'Open notes and transcript'}</small></span><time>{new Date(note.created_at).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}</time><ChevronRight size={14} /></button>)}</section>)}
      {!notes.length && !library.error && <div className="xx-library-empty"><FileText size={28} /><h2>{library.loading ? 'Opening your notes…' : query ? 'No matching notes' : 'Room for your next conversation'}</h2><p>{query ? 'Try a different title or phrase from your notes.' : 'Start a new note. xx listens while you write.'}</p></div>}</>}
    </div>
    <footer className="xx-library-footer">{library.meetings.length} notes · saved on this laptop<span>New note starts recording</span></footer>
    <Dialog open={projectDialog !== null} onOpenChange={open => { if (!open && !saving) setProjectDialog(null); }}><DialogContent className="max-w-sm"><DialogTitle>{projectDialog === 'new' ? 'New project' : 'Rename project'}</DialogTitle><DialogDescription>Keep related notes and references together.</DialogDescription><form onSubmit={event => { event.preventDefault(); void saveProject(); }}><input autoFocus className="xx-project-name-input" aria-label="Project name" value={name} maxLength={160} onChange={event => setName(event.target.value)} placeholder="Project name" /><div className="mt-4 flex justify-end gap-2"><button type="button" className="xx-button-secondary" disabled={saving} onClick={() => setProjectDialog(null)}>Cancel</button><button className="xx-button-primary" disabled={saving || !name.trim()}>{saving ? 'Saving…' : 'Save project'}</button></div></form></DialogContent></Dialog>
  </div>;
}
