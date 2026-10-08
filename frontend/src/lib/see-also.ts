import type { SeeAlsoLabel, StatedEdge } from './api-types'

/** Each see-also label as read from the entity on its left, in the order lists show them. */
export const SEE_ALSO_LABELS: Record<SeeAlsoLabel, string> = {
  later_name: 'Later name',
  earlier_name: 'Earlier name',
  part_of: 'Part of',
  has_part: 'Has part',
  member_of: 'Member of',
  has_member: 'Has member',
  leader_of: 'Leader of',
  led_by: 'Led by',
  related: 'Related',
}

/** A stated link's label as it reads between its two entities: "later name". */
export const statedText = (edge: StatedEdge) => SEE_ALSO_LABELS[edge.label].toLowerCase()
