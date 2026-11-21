/** Client-side checks for the create-playgroup form (S-2.4.1); the API stays authoritative. */

const NAME_MIN = 3;
const NAME_MAX = 50;
const DESCRIPTION_MAX = 500;

/** Length in code points, matching the API's Python `len()`. */
const count = (value: string) => [...value].length;

export interface PlaygroupErrors {
  name?: string;
  description?: string;
}

/** Name 3 to 50 after trimming, description up to 500. */
export function validatePlaygroup(name: string, description: string): PlaygroupErrors {
  const errors: PlaygroupErrors = {};
  const length = count(name.trim());
  if (length < NAME_MIN || length > NAME_MAX) {
    errors.name = `Name must be ${String(NAME_MIN)} to ${String(NAME_MAX)} characters.`;
  }
  if (count(description) > DESCRIPTION_MAX) {
    errors.description = `Description must be at most ${String(DESCRIPTION_MAX)} characters.`;
  }
  return errors;
}
