/**
 * English mirror of `components/rules/ruleSections.ts` — same shape, same ids.
 * The zh file stays the source of truth; keep both in sync when rules change
 * (`docs/gameplay.md` is the third copy that must move together).
 */
import type { RuleSection } from '../components/rules/ruleSections';

export const RULES_SUBTITLE_EN = 'Every player is controlled by the model configured when the game was created — no human players. The rules below are what the current version actually executes.';

export const RULE_SECTIONS_EN: readonly RuleSection[] = [
  {
    id: 'night',
    title: 'Night flow',
    intro: 'At night the engine runs each scheduling point in a fixed order; every role acts via its model.',
    ordered: true,
    items: [
      'The Guard wakes first: protect one living player, or choose not to (cannot protect the same player two nights in a row).',
      'The wolf pack discusses and votes one by one to pick the night kill; the White Wolf King and the Wolf Beauty share the same kill and ballot as ordinary wolves.',
      'The Witch sees the night kill and acts: save the victim with the antidote, poison a living player, or use nothing (at most one potion per night).',
      'The Wolf Beauty (when present) may additionally charm a good player after the wolf kill — or skip it.',
      'Check phase (when present, in order): the Seer checks alignment; Pure White / Wolf Witch check the exact role, and from night 2 onward an execution happens the same night when a specific role is found (Pure White kills a werewolf, the Wolf Witch kills Pure White); the Demon Hunter hunts one player from night 2, resolved the next day; the Nightmare acts earliest, fearing one player so they cannot act that night — fearing a werewolf stops the whole pack that night; the Dancer picks a trio to dance from night 2, and the side with fewer members is out.',
      'Settlement: resolve all pending damage and protection at once, announce the night deaths, and trigger death reactions such as the Hunter shot.',
    ],
  },
  {
    id: 'day',
    title: 'Day flow',
    intro: 'The day is a loop of speeches and votes; with a Sheriff, the first night ends with a sheriff election before deaths are announced.',
    ordered: true,
    items: [
      'Announce night deaths. With a Sheriff, the election happens right after the first night, before the day report.',
      'Players killed by the wolf kill or poison on the first night give last words (night deaths from night 2 onward do not).',
      'Ordered speeches: without a Sheriff, speaking starts from the living player after the most recent death and proceeds by seat; with a Sheriff, the Sheriff chooses sheriff-left/sheriff-right (peaceful or multi-kill night) or death-left/death-right (single night kill). Before each player speaks, the engine settles the day-action window once (the White Wolf King self-explosion fires here).',
      'After all speeches and before voting, a post-speech window settles (the Knight duel fires here), and daytime damage plus delayed deaths land.',
      'Exile vote: the top-voted player first passes the exile-verdict window (the Idiot can reveal to survive), then gives last words; an exiled Sheriff first decides whom to pass the badge to, or tears it.',
    ],
  },
  {
    id: 'last-words',
    title: 'Last words',
    items: [
      'Players killed by the wolf kill or the Witch\'s poison on the first night give last words; night deaths from night 2 onward do not.',
      'An exiled player gives last words in any round.',
      'A werewolf killed by the Knight\'s duel gives last words before night falls.',
      'No last words: players taken by the Hunter\'s shot, both sides of a White Wolf King self-explosion, players taken by the Wolf Beauty\'s charm revenge, and the Knight himself when he dies in disgrace.',
      'The Idiot surviving exile via reveal is not a death and has no last-words step.',
    ],
  },
  {
    id: 'ballot',
    title: 'Voting and ties',
    items: [
      'The top vote-getter is exiled; a tie goes to exactly one round of supplementary speeches plus a re-vote restricted to the tied candidates — another tie means nobody is exiled. The Crow\'s cursed seat gets +1 vote in this exile tally (including the re-vote); the Sheriff bonus does not stack on it.',
      'With a living Sheriff, their vote counts as 1.5 (tracked internally as a 3:2 integer weight).',
      'Eligibility comes from runtime status: seats that lost the vote (e.g. a revealed Idiot) no longer vote, and exile-immune seats are absent from the candidates.',
    ],
  },
  {
    id: 'win',
    title: 'Victory conditions',
    intro: 'Defaults to edge-victory and checks werewolves first: when both sides qualify simultaneously, werewolves win (the wolf kill lands first). The Cupid preset switches to city-victory and can produce a third party.',
    items: [
      'Edge victory — werewolves win when living gods reach 0, living villagers reach 0, or living wolves outnumber living good players (any one suffices). Good wins when living wolves reach 0.',
      'City victory (choose "city" when creating the game): wolves win if all good players die; good wins if all wolves die. An empty city still counts as a werewolf win (wolf kill first).',
      'While any third-party player is alive, neither wolves nor good can win; if every survivor is third-party, the third party wins. When Cupid links a good player and a wolf as lovers, Cupid and that pair count as third-party; the Admirer always counts as good (villager) — only its personal win/loss follows the crush target: it wins when the target\'s side wins, regardless of whether the target is out.',
      'Gods include the Seer, Witch, Hunter, Guard, Idiot, Knight, Pure White, Demon Hunter, Dream Weaver, Gravekeeper, Dancer, Fool Seer, and Crow; the Old Drunkard, Iron Villager, and Admirer count as villagers; werewolves include the White Wolf King, Wolf King, Evil Knight, Wolf Beauty, Gargoyle, Wolf Witch, Crimson Envoy, Nightmare, and Mask. Cupid belongs to neither gods, villagers, nor the wolf pack.',
    ],
  },
  {
    id: 'options',
    title: 'Optional rules and house differences',
    intro: 'The options below can be changed when creating a game; the remaining differences are deliberate in this project.',
    items: [
      'Sheriff (off by default, recommended on 12-player boards): running for or withdrawing from the election forfeits your sheriff vote — only players who never ran can vote; a dead Sheriff may pass or tear the badge, and the badge is lost if undecided in time.',
      'Victory mode (edge by default): the Cupid preset uses city victory — one side must be fully eliminated; with a third party alive, neither other side can win until it is gone.',
      'Open cards (off by default): when on, a player\'s role is revealed as soon as they are out.',
      'Differences from common rules: the Knight gives no last words when dying in disgrace; a Hunter killed by the Knight\'s duel cannot shoot; a revealed Idiot only loses the vote and exile eligibility — it can still speak normally.',
    ],
  },
];
