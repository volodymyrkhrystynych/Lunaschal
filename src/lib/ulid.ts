// Minimal ULID generator so offline-created records get a real, server-shaped
// id up front (the backend accepts a client-supplied `id` for these). ULIDs are
// 26-char Crockford base32: a 48-bit millisecond timestamp (10 chars) + 80 bits
// of randomness (16 chars), lexicographically sortable by creation time — the
// same format `python-ulid` produces server-side, so client and server ids
// interleave correctly under `ORDER BY id`.

const ENCODING = '0123456789ABCDEFGHJKMNPQRSTVWXYZ'; // Crockford base32 (no I,L,O,U)
const ENCODING_LEN = ENCODING.length; // 32
const TIME_LEN = 10;
const RANDOM_LEN = 16;

function encodeTime(time: number): string {
  let out = '';
  for (let i = TIME_LEN - 1; i >= 0; i--) {
    const mod = time % ENCODING_LEN;
    out = ENCODING[mod] + out;
    time = (time - mod) / ENCODING_LEN;
  }
  return out;
}

function encodeRandom(): string {
  const bytes = new Uint8Array(RANDOM_LEN);
  crypto.getRandomValues(bytes);
  let out = '';
  for (let i = 0; i < RANDOM_LEN; i++) {
    // 256 is an exact multiple of 32, so `byte % 32` is unbiased.
    out += ENCODING[bytes[i] % ENCODING_LEN];
  }
  return out;
}

export function ulid(seedTime: number = Date.now()): string {
  return encodeTime(seedTime) + encodeRandom();
}
