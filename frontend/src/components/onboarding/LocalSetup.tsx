'use client';
import { useEffect, useState } from 'react';
import { invoke } from '@tauri-apps/api/core';
import type { LocalProfile } from '@/lib/local-transcription-workflow';
import { useOnboarding } from '@/contexts/OnboardingContext';
import { Button } from '@/components/ui/button';
import { Brand } from '@/components/Brand';

export function LocalSetup({ onComplete }: { onComplete: () => void }) {
  const { databaseExists, completeOnboarding } = useOnboarding();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [profiles, setProfiles] = useState<LocalProfile[]>([]);
  const check = () => invoke<LocalProfile[]>('get_local_stt_profiles').then(result => setProfiles(result.filter(profile => ['trelis-20', 'apex-20'].includes(profile.id)))).catch(reason => setError(String(reason)));
  useEffect(() => { void check(); }, []);
  const finish = async () => {
    setBusy(true);
    try { await completeOnboarding(); onComplete(); }
    catch (reason) { setError(String(reason)); setBusy(false); }
  };
  return <main className="mx-auto my-10 max-w-xl p-9 space-y-6 xx-paper">
    <Brand tagline />
    <h1 className="xx-heading text-3xl">A home for your conversations.</h1>
    <p className="text-gray-600 leading-7">Record microphone and system audio. Trelis transcribes in 20-second windows plus processing time, then finishes the remaining audio after the call. Apex is available as a separate fallback version. Review the transcript here, then hand it to Claude when you’re ready.</p>
    <ul className="list-disc pl-5 space-y-3 text-sm text-gray-700"><li>Audio recording works independently of transcription workers.</li><li>Trelis keeps its original Hindi and English script.</li><li>Summary models are not downloaded. You choose when to copy or attach a transcript in Claude.</li></ul>
    <div className="rounded-xl border border-[var(--xx-border)] bg-[#fcf3f6] p-4 space-y-3"><h2 className="xx-eyebrow">Models on this device</h2>{profiles.map(profile => <div key={profile.id} className="text-sm"><p>{profile.model} · 20s — {profile.available ? 'Ready' : 'Setup needed'}</p>{!profile.available && <p className="text-xs text-amber-800 mt-1">{profile.reason}</p>}</div>)}<button type="button" className="text-xs text-[var(--xx-accent)] underline" onClick={() => void check()}>Check again</button><p className="text-xs text-[var(--xx-muted)]">You can open the workspace and record while model setup is incomplete.</p></div>
    {error && <p role="alert" className="text-sm text-red-700">{error}</p>}
    <Button disabled={!databaseExists || busy} onClick={() => void finish()}>{busy ? 'Opening…' : databaseExists ? 'Open workspace' : 'Preparing local database…'}</Button>
  </main>;
}
