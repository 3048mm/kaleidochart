import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import { MarketPhaseMeter } from '../MarketPhaseMeter';

describe('MarketPhaseMeter', () => {
  it('shows a neutral "判定不能" state and does not highlight Bear Market when phase is UNKNOWN', () => {
    render(<MarketPhaseMeter phase="UNKNOWN" />);

    // 判定不能であることが分かる表示がある
    expect(screen.getByText('判定不能')).not.toBeNull();

    // Bear Market に断定表示（ハイライト）されない
    const bearLabel = screen.getByText('Bear Market');
    expect(bearLabel.style.fontWeight).not.toBe('bold');
    expect(bearLabel.style.color).not.toBe('rgb(255, 255, 255)');

    // どのラベルもハイライトされていない（中立表示）
    for (const label of [
      screen.getByText('Bear Market'),
      screen.getByText('Rally Attempt'),
      screen.getByText('Correction'),
      screen.getByText('Confirmed Uptrend'),
    ]) {
      expect(label.style.fontWeight).not.toBe('bold');
    }
  });

  it('keeps BEAR phase display unchanged (regression)', () => {
    render(<MarketPhaseMeter phase="BEAR" />);

    expect(screen.queryByText('判定不能')).toBeNull();

    const bearLabel = screen.getByText('Bear Market');
    expect(bearLabel.style.fontWeight).toBe('bold');
    expect(bearLabel.style.color).toBe('rgb(255, 255, 255)');
  });

  it('keeps BULL phase display unchanged (regression)', () => {
    render(<MarketPhaseMeter phase="BULL" />);

    expect(screen.queryByText('判定不能')).toBeNull();

    const bullLabel = screen.getByText('Confirmed Uptrend');
    expect(bullLabel.style.fontWeight).toBe('bold');
    expect(bullLabel.style.color).toBe('rgb(255, 255, 255)');

    // BULL 以外のラベルはハイライトされない
    const bearLabel = screen.getByText('Bear Market');
    expect(bearLabel.style.fontWeight).not.toBe('bold');
  });

  it('keeps CORRECTION and RALLY_ATTEMPT phase display unchanged (regression)', () => {
    render(<MarketPhaseMeter phase="CORRECTION" />);
    expect(screen.queryByText('判定不能')).toBeNull();
    const correctionLabel = screen.getByText('Correction');
    expect(correctionLabel.style.fontWeight).toBe('bold');
  });
});
