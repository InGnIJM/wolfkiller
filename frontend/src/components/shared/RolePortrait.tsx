import { Box } from '@mui/material';

import { CARD_BACK, INK, roleArt } from '../../theme/tokens';
import { roleMetaFor } from './roleMeta';

interface Props {
  roleId: string | null | undefined;
  /**
   * 立绘条宽度。创建向导 64、规则页 56；填满父容器时传 `'100%'`
   * （座位卡就是这一种，它由外层 `position: absolute; inset: 0` 定尺寸）。
   */
  width?: number | string;
  /** 高度；不传则撑满父容器（列表行里就是这一种）。 */
  height?: number | string;
  /**
   * 图片替代文本。
   *
   * 默认空串：调用方几乎都在旁边已经写了角色名，图是装饰性的，再念一遍角色名
   * 只会让读屏重复。只有当立绘是唯一标签时才传。
   */
  alt?: string;
  /** 在卡背上叠一层角色名水印，默认开。 */
  showLabel?: boolean;
}

/**
 * 角色立绘。全站唯一的取图入口。
 *
 * 没有立绘的角色显示卡背 + 角色名水印，而不是借用配色相近的角色的图。
 * 立绘补齐后只需在 `ROLE_ART_BY_ID` 加一行，这里与所有调用方都不用改。
 */
export default function RolePortrait({
  roleId, width, height, alt = '', showLabel = true,
}: Props) {
  const art = roleArt(roleId);
  const missing = art === CARD_BACK;
  const label = roleMetaFor(roleId)?.label ?? '未知身份';

  return (
    <Box
      sx={{
        position: 'relative',
        width,
        height,
        flexShrink: 0,
        overflow: 'hidden',
        // 预留尺寸，避免图片解码完成时撑动列表（CLS）。
        aspectRatio: width && !height ? '3 / 4' : undefined,
        bgcolor: 'background.default',
      }}
    >
      <Box
        component="img"
        src={art}
        alt={alt}
        loading="lazy"
        decoding="async"
        sx={{
          position: 'absolute',
          inset: 0,
          width: '100%',
          height: '100%',
          objectFit: 'cover',
          objectPosition: 'center 16%',
        }}
      />
      {missing && showLabel && (
        <Box
          aria-hidden
          sx={{
            position: 'absolute',
            inset: 0,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            px: 0.5,
            textAlign: 'center',
          }}
        >
          <Box
            component="span"
            sx={{
              fontSize: 10,
              lineHeight: 1.2,
              color: INK.faint,
              letterSpacing: '0.02em',
            }}
          >
            {label}
          </Box>
        </Box>
      )}
    </Box>
  );
}
