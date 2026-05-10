interface Props {
  role: string | null;
  size?: number;
}

const ROLE_EMOJI: Record<string, string> = {
  'wolf-killer-werewolf': '🐺',
  'wolf-killer-villager': '👤',
  'wolf-killer-seer': '🔮',
  'wolf-killer-witch': '🧪',
  'wolf-killer-hunter': '🔫',
};

export default function RoleIcon({ role, size = 28 }: Props) {
  if (!role) return null;
  const emoji = ROLE_EMOJI[role] || '❓';
  return (
    <span style={{ fontSize: size, lineHeight: 1 }} title={role}>
      {emoji}
    </span>
  );
}
