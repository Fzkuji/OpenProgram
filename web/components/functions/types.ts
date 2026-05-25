/**
 * Shared types for the /functions page.
 */

export type FunctionInfo = {
  name: string;
  category?: string;
  description?: string;
  mtime?: number;
};

export interface FunctionsMeta {
  favorites: string[];
  folders: Record<string, string[]>;
  icons: Record<string, string>;
}
