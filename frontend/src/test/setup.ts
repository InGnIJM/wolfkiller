/**
 * Vitest setup：组件单测渲染前必须完成 i18n 初始化，否则
 * `useTranslation()` 拿不到实例。
 *
 * 测试默认语言钉死 zh-CN：jsdom 的 `navigator.language` 是 en-US，
 * 「跟随系统」会解析成英文，让按中文文案断言的测试全变红。需要验证
 * 英文渲染的测试自己 `changeLanguage` / 改 store 后再切回。
 */
import i18n from '../i18n';
import { useUiSettingsStore } from '../store/uiSettingsStore';

useUiSettingsStore.setState({ language: 'zh-CN' });
void i18n.changeLanguage('zh-CN');
