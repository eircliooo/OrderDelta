import { COVERAGE_WARNING, roleLabel, roleRank } from '../labels/enums'

interface Props {
  comparedRoles: string[]
  skippedRoles: string[]
  /** 参与比较的角色总数上限（MVP-0 = 3）。 */
  totalRoles?: number
}

/**
 * 硬约束 #4 / SPEC §1.3：**参与比较的角色少于 3 个时必须醒目显示**
 * 「缺席角色 = 未检查，不等于无差异」，并列出缺席角色。
 *
 * 这是集合驱动比较（|R| >= 2 即可跑）引入的新失效模式：
 * 只传了 PO 和 PI 的人很容易把「零差异」读成「报价单也没问题」。不得隐藏、不得折叠。
 */
export function CoverageBanner({ comparedRoles, skippedRoles, totalRoles = 3 }: Props) {
  if (comparedRoles.length >= totalRoles) return null

  const absent = [...skippedRoles].sort((a, b) => roleRank(a) - roleRank(b))
  const compared = [...comparedRoles].sort((a, b) => roleRank(a) - roleRank(b))

  return (
    <section className="banner banner-coverage" role="alert" data-testid="coverage-banner">
      <p className="banner-title">
        <span aria-hidden="true">▲ </span>
        {COVERAGE_WARNING}
      </p>
      <p className="banner-body">
        本次只比较了 {compared.length} 份单据：
        {compared.length > 0 ? compared.map(roleLabel).join('、') : '（暂无）'}。
      </p>
      <p className="banner-body">
        <strong>缺席角色：</strong>
        {absent.length > 0 ? absent.map(roleLabel).join('、') : '（无）'}
        。缺席角色上的任何内容都<strong>没有被检查过</strong>，不要据此认定它们没有问题。
      </p>
    </section>
  )
}
