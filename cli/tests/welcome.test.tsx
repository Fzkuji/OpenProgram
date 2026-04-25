import { describe, it, expect } from 'vitest';
import React from 'react';
import { render } from 'ink-testing-library';
import { Welcome } from '../src/components/Welcome.js';

const stripAnsi = (s: string): string => s.replace(/\x1b\[[0-9;?]*[A-Za-z]/g, '');

describe('Welcome', () => {
  it('shows agent / model line and tile labels', () => {
    const { lastFrame } = render(
      <Welcome
        stats={{
          agent: { id: 'main', name: 'Main', model: 'gpt-5.4' },
          agents_count: 1,
          programs_count: 21,
          skills_count: 8,
          conversations_count: 5,
          top_programs: [
            { name: 'create', category: 'builtin' },
            { name: 'edit', category: 'builtin' },
          ],
          top_skills: [{ name: 'write' }],
        }}
      />,
    );
    const out = stripAnsi(lastFrame() ?? '');
    expect(out).toContain('OpenProgram');
    expect(out).toContain('Main');
    expect(out).toContain('gpt-5.4');
    expect(out).toContain('skills');
    expect(out).toContain('agents');
    expect(out).toContain('functions');
    expect(out).toContain('applications');
  });

  it('renders em-dashes for missing counts', () => {
    const { lastFrame } = render(<Welcome stats={{}} />);
    const out = stripAnsi(lastFrame() ?? '');
    expect(out).toContain('—');
  });
});
