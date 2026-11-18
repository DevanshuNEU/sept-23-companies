import { describe, expect, it } from 'vitest';
import { splitReply } from './reply-text';

describe('splitReply', () => {
  it('cuts an Outlook quote at From: with Sent: after it', () => {
    const text =
      'Hi Devanshu, Thank you and I have forwarded your resume along. Best, Erica Leep Early Careers Coordinator @ Vizient (630) 532 9799 From: Devanshu Rajesh Chicholikar <d@x.edu> Sent: Wednesday, November 18, 2026 1:49 PM To: Leep,Erica Subject: Re: entry level engineer req';
    const { fresh, quoted } = splitReply(text);
    expect(fresh).toBe('Hi Devanshu, Thank you and I have forwarded your resume along. Best, Erica Leep Early Careers Coordinator @ Vizient (630) 532 9799');
    expect(quoted.startsWith('From: Devanshu')).toBe(true);
  });

  it('cuts a Gmail quote at "On ... wrote:"', () => {
    const { fresh, quoted } = splitReply('Sounds good, Thursday works.\n\nOn Wed, Nov 18, 2026 at 1:13 PM Devanshu <d@x.com> wrote:\n> Hi Priya');
    expect(fresh).toBe('Sounds good, Thursday works.');
    expect(quoted).toMatch(/^On Wed/);
  });

  it('cuts an original-message rule', () => {
    expect(splitReply('Thanks!\n-----Original Message-----\nFrom: a').fresh).toBe('Thanks!');
  });

  it('cuts at a confidentiality footer', () => {
    const { fresh, quoted } = splitReply('Thank you for your email. Best, Pat E-MAIL CONFIDENTIALITY NOTICE: The information transmitted is private.');
    expect(fresh).toBe('Thank you for your email. Best, Pat');
    expect(quoted).toMatch(/^E-MAIL CONFIDENTIALITY NOTICE/);
  });

  it('cuts at the earliest marker', () => {
    const { fresh } = splitReply('Yes. CONFIDENTIALITY NOTICE: x From: a Sent: b');
    expect(fresh).toBe('Yes.');
  });

  it('keeps a plain message whole', () => {
    expect(splitReply('Does Thursday at 2 PT work?')).toEqual({ fresh: 'Does Thursday at 2 PT work?', quoted: '' });
  });

  it('never leaves the fresh part empty: a message that opens with a marker shows whole', () => {
    expect(splitReply('From: a Sent: b To: c')).toEqual({ fresh: 'From: a Sent: b To: c', quoted: '' });
  });

  it('a lone "From:" with no Sent: after it is not a quote', () => {
    expect(splitReply('I am writing From: the team in Irving about your role.').quoted).toBe('');
  });
});
