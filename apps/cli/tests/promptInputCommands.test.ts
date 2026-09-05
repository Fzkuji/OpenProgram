import { describe, expect, it } from 'vitest';

import { filterCommands } from '../src/components/PromptInput/PromptInput.js';

describe('prompt command completion', () => {
  it('filters by the command token without treating arguments as its name', () => {
    expect(filterCommands('/memory status').map((command) => command.name)).toEqual(['memory']);
    expect(filterCommands('/mcp add demo "C:\\Program Files\\demo.exe"').map((command) => command.name))
      .toContain('mcp');
  });
});
