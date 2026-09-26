import type { SourceTier } from './api'

export const TIER_COLORS: Record<SourceTier, string> = {
  peerReviewed: '#2f6f9f',
  preprint: '#c2703d',
  officialBlog: '#5e8c4a',
}

export const UNKNOWN_TIER_COLOR = '#8a8f98'

export const TIER_LABELS: Record<SourceTier, string> = {
  peerReviewed: 'Peer-reviewed',
  preprint: 'Preprint',
  officialBlog: 'Official blog',
}
