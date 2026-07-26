import { severityLabel, severityMark, severitySlug } from '../labels/enums'

interface Props {
  severity: string
}

/**
 * 严重度徽章。**形状记号 + 中文文字 + 颜色**三重编码。
 *
 * 只靠红/绿区分对色觉障碍用户等于没区分，所以文字标签是必选项、形状是第二重，
 * 颜色只是第三重冗余：整页灰度打印依然分得清哪条最要命。
 */
export function SeverityBadge({ severity }: Props) {
  const slug = severitySlug(severity)
  return (
    <span className={`sev sev-${slug}`}>
      <span className="sev-mark" aria-hidden="true">
        {severityMark(severity)}
      </span>
      <span className="sev-text">{severityLabel(severity)}</span>
    </span>
  )
}
