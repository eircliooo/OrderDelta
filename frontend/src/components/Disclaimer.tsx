import { DISCLAIMER } from '../labels/enums'

/**
 * 硬约束 #5：免责声明**常驻可见**。
 *
 * 放在布局 header 里，不随路由/滚动消失——这不是一次性提示，是产品边界声明。
 */
export function Disclaimer() {
  return (
    <p className="disclaimer" role="note">
      <strong>说明：</strong>
      {DISCLAIMER}
    </p>
  )
}
