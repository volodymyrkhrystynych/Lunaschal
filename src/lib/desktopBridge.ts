export interface MidiDevice {
  id: string;
  name: string;
}

export interface MidiEvent {
  kind: 'noteOn' | 'noteOff' | 'sustain' | 'error';
  note?: number;
  velocity?: number;
  value?: number;
  message?: string;
  timestampMs?: number;
}

export interface DesktopApi {
  is_desktop(): Promise<boolean>;
  midi_devices(): Promise<MidiDevice[]>;
  midi_open(path: string): Promise<{ ok: boolean; error?: string }>;
  midi_close(): Promise<{ ok: boolean }>;
}

declare global {
  interface Window {
    pywebview?: { api?: DesktopApi };
  }
}

export function desktopApi(): DesktopApi | null {
  return window.pywebview?.api ?? null;
}

export function hasDesktopBridge(): boolean {
  return desktopApi() !== null;
}

export function subscribeMidiEvents(
  listener: (events: MidiEvent[]) => void
): () => void {
  const handler = (event: Event) => {
    const detail = (event as CustomEvent<unknown>).detail;
    if (Array.isArray(detail)) listener(detail as MidiEvent[]);
  };
  window.addEventListener('lunaschal-midi', handler);
  return () => window.removeEventListener('lunaschal-midi', handler);
}
