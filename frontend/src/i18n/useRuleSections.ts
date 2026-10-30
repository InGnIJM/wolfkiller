import { useTranslation } from 'react-i18next';

import { RULES_SUBTITLE, RULE_SECTIONS } from '../components/rules/ruleSections';
import type { RuleSection } from '../components/rules/ruleSections';
import { RULES_SUBTITLE_EN, RULE_SECTIONS_EN } from './ruleSectionsEn';

export interface RuleSectionsModule {
  subtitle: string;
  sections: readonly RuleSection[];
}

/** 规则页叙述文案按界面语言取整套表；角色目录数据仍走后端。 */
export function useRuleSections(): RuleSectionsModule {
  const { i18n } = useTranslation();
  return i18n.language.startsWith('zh')
    ? { subtitle: RULES_SUBTITLE, sections: RULE_SECTIONS }
    : { subtitle: RULES_SUBTITLE_EN, sections: RULE_SECTIONS_EN };
}
