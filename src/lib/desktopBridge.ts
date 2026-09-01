export interface MidiDevice {
  id: string;
  name: string;
}

export interface DesktopApi {
  is_desktop(): Promise<boolean>;
  midi_devices(): Promise<MidiDevice[]>;
  midi_open(path: string): Promise<{ ok: boolean; error?: string }>;
  midi_poll(): Promise<{
    connected: boolean;
    events: Array<{
      kind: 'noteOn' | 'noteOff' | 'sustain' | 'error';
      note?: number;
      velocity?: number;
      value?: number;
      message?: string;
    }>;
  }>;
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
