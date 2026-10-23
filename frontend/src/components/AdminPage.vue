<template>
  <div class="min-h-[calc(100vh-4rem)]">
    <div class="max-w-6xl mx-auto px-4 sm:px-6 py-8">
      <!-- 页头 -->
      <div class="flex items-center gap-3 mb-6">
        <button
          type="button"
          @click="emit('back')"
          aria-label="返回主站"
          class="w-9 h-9 rounded-xl bg-panel border border-line flex items-center justify-center text-gray-500 hover:text-gray-800 hover:border-gray-300 transition-colors"
        >
          <svg
            class="w-4 h-4"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            stroke-width="2"
            stroke-linecap="round"
            stroke-linejoin="round"
            aria-hidden="true"
          >
            <path d="M19 12H5" />
            <path d="M12 19l-7-7 7-7" />
          </svg>
        </button>
        <div>
          <h1 class="text-xl font-bold text-gray-900">管理后台</h1>
          <p class="text-xs text-gray-400 mt-0.5">
            用户额度、社区记录与 AI 服务清单。账号增删与管理员标记也在这里（ADR 0012）
          </p>
        </div>
      </div>

      <!-- 非管理员：后端 403 的可读错误态。
           前端隐藏入口只做体验，真正的边界是后端 require_admin（ADR 0010）——
           所以这一段不是「防谁」的，是「别让人撞上白屏」的。 -->
      <div v-if="view.forbidden" class="rounded-2xl bg-panel border border-line p-8 text-center" role="alert">
        <p class="text-sm text-red-600">没有管理员权限</p>
        <p class="text-xs text-gray-400 mt-1">这个页面只对管理员开放。服务端已拒绝本次请求，请换一个管理员账号登录。</p>
        <button
          type="button"
          @click="emit('back')"
          class="mt-4 px-3 py-1.5 rounded-lg bg-panel border border-line text-xs text-gray-500 hover:border-gray-300 transition-colors"
        >
          返回主站
        </button>
      </div>

      <template v-else>
        <!-- 页签：全部是原生 button，所以 Tab 能逐个走到；
             左右方向键额外可用，不靠它们才能操作。 -->
        <div
          :id="`admin-panel-${tab}`"
          role="tabpanel"
          :aria-labelledby="`admin-tab-${tab}`"
          :aria-busy="view.loading ? 'true' : 'false'"
        >
          <div
            class="flex flex-wrap items-center gap-2 mb-6 border-b border-line pb-3"
            role="tablist"
            aria-label="管理后台页签"
          >
            <button
              v-for="t in TABS"
              :key="t.key"
              type="button"
              role="tab"
              :id="`admin-tab-${t.key}`"
              :aria-controls="`admin-panel-${t.key}`"
              :aria-selected="tab === t.key ? 'true' : 'false'"
              @click="selectTab(t.key)"
              @keydown="onTabKeydown(t.key, $event)"
              :class="[
                'px-3.5 py-1.5 rounded-lg text-sm border transition-colors',
                tab === t.key
                  ? 'bg-blue text-on-primary border-blue font-medium'
                  : 'bg-panel text-gray-500 border-line hover:border-gray-300 hover:text-gray-800',
              ]"
            >
              {{ t.label }}
            </button>
          </div>

          <!-- 三个状态：加载中 / 故障 / 空。放在内容**之前**——
             否则一次 500 会被渲染成「没有数据」，用户会以为该换个关键词再搜，
             真正的原因被吞掉了。四个页签共用这一块（每个页签有自己的一份状态）。 -->
          <div v-if="view.loading" class="space-y-3" role="status" aria-live="polite">
            <span class="sr-only">正在加载{{ tabLabel }}</span>
            <div v-for="n in 5" :key="n" class="skeleton h-16 rounded-2xl"></div>
          </div>

          <div v-else-if="view.error" class="rounded-2xl bg-panel border border-line py-16 text-center" role="alert">
            <p class="text-sm text-red-600">{{ tabLabel }}加载失败</p>
            <p class="text-xs text-gray-400 mt-1">{{ view.error }}</p>
            <button
              type="button"
              @click="reload"
              class="mt-4 px-3 py-1.5 rounded-lg bg-panel border border-line text-xs text-gray-500 hover:border-gray-300 transition-colors"
            >
              重试
            </button>
          </div>

          <div v-else-if="!view.items.length" class="py-16 text-center text-gray-400">
            <p class="text-sm">{{ empty.text }}</p>
            <p class="text-xs text-gray-400 mt-1">{{ empty.hint }}</p>
          </div>

          <template v-else>
            <!-- ══ 用户。编辑区按行展开，不跟页签走。
               原来还有一个「额度」页签，它就是把编辑控件常驻行内的那一版，
               与本页同一个接口、同一份数据。用户页本来就能改额度，
               同一个动作就不该有两个入口。 ══ -->
            <div v-if="tab === 'users'">
              <form @submit.prevent="search" class="flex flex-wrap items-center gap-2 mb-4">
                <label for="admin-user-q" class="text-xs text-gray-500">按邮箱搜</label>
                <input
                  id="admin-user-q"
                  v-model="query"
                  type="search"
                  placeholder="邮箱片段"
                  class="w-64 px-3 py-2 rounded-xl bg-panel border border-line text-sm text-gray-800 placeholder-gray-400 focus:border-blue-200 focus:outline-none"
                />
                <button
                  type="submit"
                  :disabled="view.loading"
                  class="px-4 py-2 rounded-xl bg-blue text-on-primary text-sm font-medium hover:bg-blue-600 transition-colors disabled:opacity-50"
                >
                  搜索
                </button>
                <button
                  v-if="query"
                  type="button"
                  @click="clearQuery"
                  class="px-3 py-2 rounded-xl text-xs text-gray-400 hover:text-gray-700 hover:bg-panel transition-colors"
                >
                  清除
                </button>
              </form>

              <!-- ══ 建号（ADR 0012）。只挂在用户页：额度页是「改」的那一版，
                 放两个入口会让同一个动作有两个地方能点。 ══ -->
              <div class="mb-4">
                <button
                  type="button"
                  @click="toggleCreate"
                  :aria-expanded="createOpen ? 'true' : 'false'"
                  aria-controls="admin-create-user"
                  class="px-3 py-2 rounded-xl border border-line text-xs text-gray-500 hover:border-blue-200 hover:text-blue-600 transition-colors"
                >
                  {{ createOpen ? '收起新建' : '新建用户' }}
                </button>

                <form
                  v-if="createOpen"
                  id="admin-create-user"
                  class="mt-3 flex flex-wrap items-end gap-3"
                  @submit.prevent="saveNewUser"
                >
                  <div>
                    <label for="admin-new-email" class="block text-xs text-gray-500 mb-1">邮箱</label>
                    <input
                      id="admin-new-email"
                      v-model="createDraft.email"
                      type="email"
                      required
                      autocomplete="off"
                      class="w-64 px-3 py-2 rounded-xl bg-panel border border-line text-sm text-gray-800 placeholder-gray-400 focus:border-blue-200 focus:outline-none"
                    />
                  </div>
                  <div>
                    <label for="admin-new-password" class="block text-xs text-gray-500 mb-1">初始密码</label>
                    <input
                      id="admin-new-password"
                      v-model="createDraft.password"
                      type="password"
                      required
                      minlength="6"
                      autocomplete="new-password"
                      :aria-describedby="`admin-new-password-hint-${''}`"
                      class="w-48 px-3 py-2 rounded-xl bg-panel border border-line text-sm text-gray-800 focus:border-blue-200 focus:outline-none"
                    />
                    <p id="admin-new-password-hint-" class="mt-1 text-[11px] text-gray-400">
                      至少 6 位。由你转交给对方——项目当前没有改密入口。
                    </p>
                  </div>
                  <div class="flex items-center gap-1.5 text-xs text-gray-600 pb-2">
                    <input id="admin-new-isadmin" v-model="createDraft.isAdmin" type="checkbox" class="accent-blue" />
                    <label for="admin-new-isadmin">直接给管理员权限</label>
                  </div>
                  <button
                    type="submit"
                    :disabled="creating"
                    class="px-4 py-2 rounded-xl bg-blue text-on-primary text-sm font-medium hover:bg-blue-600 transition-colors disabled:opacity-50"
                  >
                    {{ creating ? '创建中…' : '创建' }}
                  </button>
                </form>
                <!-- 反馈在表单**外面**：成功时 createOpen 会被置 false，
                   放在表单里等于把「已创建 X」这条确认一起收走，
                   管理员会以为自己没点上（与额度编辑器同一条纪律）。 -->
                <span aria-live="polite" :class="['block mt-2 text-xs', feedbackClass(createFeedback.kind)]">
                  {{ createFeedback.text }}
                </span>
              </div>

              <div class="overflow-x-auto rounded-2xl border border-line bg-panel">
                <table class="w-full text-sm">
                  <caption class="sr-only">
                    用户列表，可修改解析与追问的额度上限
                  </caption>
                  <thead>
                    <tr class="text-left text-xs text-gray-400 border-b border-line">
                      <th scope="col" class="px-4 py-3 font-medium">邮箱</th>
                      <th scope="col" class="px-4 py-3 font-medium">标记</th>
                      <th scope="col" class="px-4 py-3 font-medium">解析</th>
                      <th scope="col" class="px-4 py-3 font-medium">追问</th>
                      <th scope="col" class="px-4 py-3 font-medium">注册时间</th>
                      <th scope="col" class="px-4 py-3 font-medium">账号</th>
                    </tr>
                  </thead>
                  <tbody>
                    <template v-for="u in view.items as AdminUser[]" :key="u.id">
                      <tr
                        class="border-b border-line last:border-b-0 cursor-pointer"
                        :class="editorOpen(u) ? 'bg-panel-2' : ''"
                        @click="toggleRow(u)"
                      >
                        <td class="px-4 py-3 font-pixel text-xs text-gray-800">
                          <button
                            type="button"
                            class="flex items-center gap-1.5 w-full text-left"
                            :aria-expanded="editorOpen(u) ? 'true' : 'false'"
                            :aria-controls="`quota-editor-${u.id}`"
                            @click.stop="toggleRow(u)"
                          >
                            <span aria-hidden="true" class="text-gray-400 w-2">{{ editorOpen(u) ? '▾' : '▸' }}</span>
                            <span class="truncate">{{ u.email }}</span>
                          </button>
                        </td>
                        <td class="px-4 py-3">
                          <span class="flex flex-wrap gap-1.5">
                            <span
                              v-if="u.isAdmin"
                              class="text-[10px] font-medium bg-blue-50 text-blue-600 border border-blue-200 px-1.5 py-0.5 rounded"
                              >管理员</span
                            >
                            <span
                              v-if="u.isVip"
                              class="text-[10px] font-medium bg-amber-50 text-amber-600 border border-amber-200 px-1.5 py-0.5 rounded"
                              >VIP</span
                            >
                            <span v-if="!u.isAdmin && !u.isVip" class="text-[10px] text-gray-400">普通用户</span>
                          </span>
                        </td>
                        <td class="px-4 py-3 text-xs font-pixel text-gray-600">
                          {{ counter(u.parseUsed, u.parseLimit, u.parseLimitSource) }}
                        </td>
                        <td class="px-4 py-3 text-xs font-pixel text-gray-600">
                          {{ counter(u.chatUsed, u.chatLimit, u.chatLimitSource) }}
                        </td>
                        <td class="px-4 py-3 text-xs text-gray-400">{{ u.createdAt }}</td>
                        <td class="px-4 py-3">
                          <div class="flex flex-wrap items-center gap-1.5">
                            <button
                              type="button"
                              @click.stop="toggleAdmin(u)"
                              :disabled="busyUserId !== null"
                              class="px-2.5 py-1 rounded-lg border border-line text-xs text-gray-500 hover:border-blue-200 hover:text-blue-600 transition-colors disabled:opacity-50"
                            >
                              {{ u.isAdmin ? '取消管理员' : '设为管理员' }}
                            </button>
                            <button
                              v-if="pendingDeleteId !== u.id"
                              type="button"
                              @click.stop="askDelete(u)"
                              :disabled="busyUserId !== null"
                              class="px-2.5 py-1 rounded-lg border border-line text-xs text-red-500 hover:border-red-200 transition-colors disabled:opacity-50"
                            >
                              删除
                            </button>
                          </div>
                          <span
                            aria-live="polite"
                            :class="['block mt-1 text-[11px]', feedbackClass(opsFeedbackOf(u.id).kind)]"
                          >
                            {{ opsFeedbackOf(u.id).text }}
                          </span>
                        </td>
                      </tr>

                      <!-- 删除二次确认。两步而不是 window.confirm：
                         同一个可测状态机，样式统一，也不会把用户卡在模态框里。 -->
                      <tr v-if="pendingDeleteId === u.id" class="bg-red-50">
                        <td colspan="6" class="px-4 py-3">
                          <p class="text-xs text-red-700">
                            确认删除 {{ u.email }}？名下有订单或解析历史时会被服务端拒绝（409），
                            社区视频不受影响。此操作不可撤销。
                          </p>
                          <div class="mt-2 flex items-center gap-2">
                            <button
                              type="button"
                              @click="confirmDelete(u)"
                              :disabled="busyUserId !== null"
                              class="px-3 py-1.5 rounded-lg bg-red-600 text-on-solid text-xs hover:bg-red-700 transition-colors disabled:opacity-50"
                            >
                              {{ busyUserId === u.id ? '删除中…' : '确认删除' }}
                            </button>
                            <button
                              type="button"
                              @click="cancelDelete"
                              class="px-3 py-1.5 rounded-lg bg-panel border border-line text-xs text-gray-500 hover:border-gray-300 transition-colors"
                            >
                              取消
                            </button>
                          </div>
                        </td>
                      </tr>

                      <!-- 行内额度编辑器。额度页常驻，用户页按需展开。 -->
                      <tr v-if="editorOpen(u)" :id="`quota-editor-${u.id}`" class="bg-panel-2">
                        <td colspan="6" class="px-4 py-3">
                          <div class="flex flex-wrap items-end gap-3">
                            <div>
                              <label :for="`quota-parse-${u.id}`" class="block text-xs text-gray-500 mb-1"
                                >解析上限</label
                              >
                              <input
                                :id="`quota-parse-${u.id}`"
                                v-model="drafts[u.id].parse"
                                type="number"
                                min="-1"
                                step="1"
                                :aria-describedby="`quota-hint-${u.id}`"
                                class="w-32 px-3 py-1.5 rounded-lg bg-panel border border-line text-sm font-pixel text-gray-800 focus:border-blue-200 focus:outline-none"
                              />
                            </div>
                            <div>
                              <label :for="`quota-chat-${u.id}`" class="block text-xs text-gray-500 mb-1"
                                >追问上限</label
                              >
                              <input
                                :id="`quota-chat-${u.id}`"
                                v-model="drafts[u.id].chat"
                                type="number"
                                min="-1"
                                step="1"
                                :aria-describedby="`quota-hint-${u.id}`"
                                class="w-32 px-3 py-1.5 rounded-lg bg-panel border border-line text-sm font-pixel text-gray-800 focus:border-blue-200 focus:outline-none"
                              />
                            </div>
                            <button
                              type="button"
                              @click="saveQuota(u)"
                              :disabled="savingId === u.id"
                              class="px-3 py-1.5 rounded-lg bg-blue text-on-primary text-xs font-medium hover:bg-blue-600 transition-colors disabled:opacity-50"
                            >
                              {{ savingId === u.id ? '保存中…' : '保存' }}
                            </button>
                            <button
                              type="button"
                              @click="closeEditor(u.id)"
                              class="px-3 py-1.5 rounded-lg bg-panel border border-line text-xs text-gray-500 hover:border-gray-300 transition-colors"
                            >
                              取消
                            </button>
                          </div>
                          <p :id="`quota-hint-${u.id}`" class="mt-2 text-xs text-gray-400">
                            留空 = 清除覆盖、回落全局；0 = 一条都不能用；-1 = 无限。
                            <span v-if="u.isVip">该用户是有效 VIP，走无限额度——这里的改动不会生效。</span>
                          </p>
                          <!-- 改额度必须有成功与失败两条反馈。不给失败反馈等于让管理员
                             以为是自己操作错了（真实原因在服务端，日志里才有）。 -->
                          <p
                            v-if="feedbackOf(u.id).text"
                            :id="`quota-fb-${u.id}`"
                            role="status"
                            aria-live="polite"
                            class="mt-2 text-xs"
                            :class="feedbackClass(feedbackOf(u.id).kind)"
                          >
                            {{ feedbackOf(u.id).text }}
                          </p>
                        </td>
                      </tr>
                    </template>
                  </tbody>
                </table>
              </div>
            </div>

            <!-- ══ 社区：可改标签、可删条目（ADR 0013）。
               删只删 videos 那一行：解析过它的用户在自己「历史」里的记录不受影响——
               库里没有任何外键指向 videos，这正是该语义成立的前提。 ══ -->
            <div v-else-if="tab === 'community'">
              <p class="mb-3 text-xs text-gray-400">
                点任意一行改标签。删除只移除社区条目，
                <strong class="font-medium text-gray-600">不删用户的解析历史</strong
                >——用户在自己历史里仍看得到自己的记录。此操作不可撤销。
              </p>
              <div class="overflow-x-auto rounded-2xl border border-line bg-panel">
                <table class="w-full text-sm">
                  <caption class="sr-only">
                    社区视频列表
                  </caption>
                  <thead>
                    <tr class="text-left text-xs text-gray-400 border-b border-line">
                      <th scope="col" class="px-4 py-3 font-medium">标题</th>
                      <th scope="col" class="px-4 py-3 font-medium">作者</th>
                      <th scope="col" class="px-4 py-3 font-medium">标签</th>
                      <th scope="col" class="px-4 py-3 font-medium">状态</th>
                      <th scope="col" class="px-4 py-3 font-medium">时间</th>
                      <th scope="col" class="px-4 py-3 font-medium">操作</th>
                    </tr>
                  </thead>
                  <tbody>
                    <template v-for="c in view.items as AdminCommunityItem[]" :key="c.id">
                      <tr
                        class="border-b border-line last:border-b-0 cursor-pointer"
                        :class="communityEditorOpen(c.id) ? 'bg-panel-2' : ''"
                        @click="toggleCommunityRow(c)"
                      >
                        <td class="px-4 py-3">
                          <button
                            type="button"
                            class="flex items-start gap-1.5 w-full text-left"
                            :aria-expanded="communityEditorOpen(c.id) ? 'true' : 'false'"
                            :aria-controls="`community-tags-${c.id}`"
                            @click.stop="toggleCommunityRow(c)"
                          >
                            <span aria-hidden="true" class="text-gray-400 w-2 pt-0.5">{{
                              communityEditorOpen(c.id) ? '▾' : '▸'
                            }}</span>
                            <span class="min-w-0">
                              <span class="block text-xs text-gray-800">{{ c.title || '未命名视频' }}</span>
                              <span class="block mt-0.5 text-[11px] font-pixel text-gray-400 truncate max-w-xs">{{
                                c.videoUrl
                              }}</span>
                            </span>
                          </button>
                        </td>
                        <td class="px-4 py-3 text-xs font-pixel text-gray-600">{{ c.authorEmail }}</td>
                        <td class="px-4 py-3">
                          <span class="flex flex-wrap gap-1.5">
                            <span
                              v-for="t in c.tags"
                              :key="t"
                              class="text-[10px] font-pixel bg-blue-50 text-blue-600 border border-blue-200 px-1.5 py-0.5 rounded"
                              >{{ t }}</span
                            >
                            <span v-if="!c.tags.length" class="text-[10px] text-gray-400">无标签</span>
                          </span>
                        </td>
                        <td class="px-4 py-3">
                          <span
                            class="text-[10px] px-1.5 py-0.5 rounded border"
                            :class="
                              c.status === 'ready'
                                ? 'bg-emerald-50 text-emerald-600 border-emerald-200'
                                : 'bg-amber-50 text-amber-600 border-amber-200'
                            "
                          >
                            {{ c.status === 'ready' ? '已就绪' : '占位中' }}
                          </span>
                        </td>
                        <td class="px-4 py-3 text-xs text-gray-400">{{ c.createdAt }}</td>
                        <td class="px-4 py-3">
                          <button
                            type="button"
                            @click.stop="askDeleteCommunity(c)"
                            :disabled="communityBusyId !== null"
                            class="px-2.5 py-1 rounded-lg border border-line text-xs text-red-500 hover:border-red-200 transition-colors disabled:opacity-50"
                          >
                            删除
                          </button>
                          <span
                            aria-live="polite"
                            :class="['block mt-1 text-[11px]', feedbackClass(communityFeedbackOf(c.id).kind)]"
                          >
                            {{ communityFeedbackOf(c.id).text }}
                          </span>
                        </td>
                      </tr>

                      <!-- 标签编辑器。词表从服务端取，前端不自己备份一份——
                         CommunityPage.vue 的标签筛选已经是「从已加载的卡片汇总」，
                         在前端再抄第二份就是漂移本身。 -->
                      <tr v-if="communityEditorOpen(c.id)" :id="`community-tags-${c.id}`" class="bg-panel-2">
                        <td colspan="6" class="px-4 py-3">
                          <p v-if="vocabError" class="text-xs text-red-500">{{ vocabError }}</p>
                          <p v-else-if="vocabLoading" class="text-xs text-gray-400">正在取标签词表…</p>
                          <template v-else>
                            <div v-for="g in vocabulary.groups" :key="g.name" class="mb-3 last:mb-0">
                              <p class="text-[11px] text-gray-400 mb-1.5">{{ g.name }}</p>
                              <div class="flex flex-wrap gap-1.5">
                                <label
                                  v-for="t in g.tags"
                                  :key="t"
                                  class="text-[10px] font-pixel border px-1.5 py-0.5 rounded cursor-pointer"
                                  :class="
                                    tagSelected(c.id, t)
                                      ? 'bg-blue-50 text-blue-600 border-blue-200'
                                      : 'bg-panel text-gray-500 border-line'
                                  "
                                >
                                  <input
                                    type="checkbox"
                                    class="sr-only"
                                    :checked="tagSelected(c.id, t)"
                                    :disabled="!tagSelected(c.id, t) && selectedTagCount(c.id) >= maxTags"
                                    @change="toggleTag(c.id, t)"
                                  />{{ t }}
                                </label>
                              </div>
                            </div>
                            <p class="text-xs text-gray-400">
                              最多 {{ maxTags }} 个，当前选了 {{ selectedTagCount(c.id) }} 个。
                              <span v-if="selectedTagCount(c.id) >= maxTags">已选满 —— 先取掉一个才能勾选。</span>
                            </p>
                            <div class="mt-2 flex items-center gap-2">
                              <button
                                type="button"
                                @click="saveCommunityTags(c)"
                                :disabled="communityBusyId !== null"
                                class="px-3 py-1.5 rounded-lg bg-blue text-on-primary text-xs font-medium hover:bg-blue-600 transition-colors disabled:opacity-50"
                              >
                                {{ communityBusyId === c.id ? '保存中…' : '保存标签' }}
                              </button>
                              <button
                                type="button"
                                @click="closeCommunityEditor(c.id)"
                                class="px-3 py-1.5 rounded-lg bg-panel border border-line text-xs text-gray-500 hover:border-gray-300 transition-colors"
                              >
                                取消
                              </button>
                            </div>
                          </template>
                          <p
                            v-if="communityFeedbackOf(c.id).text"
                            role="status"
                            aria-live="polite"
                            class="mt-2 text-xs"
                            :class="feedbackClass(communityFeedbackOf(c.id).kind)"
                          >
                            {{ communityFeedbackOf(c.id).text }}
                          </p>
                        </td>
                      </tr>

                      <!-- 删除二次确认。文案里明写「不删用户历史」：
                         这份数据在删完之后看不到任何地方，只能在点之前说。 -->
                      <tr v-if="pendingCommunityDeleteId === c.id" class="bg-red-50">
                        <td colspan="6" class="px-4 py-3">
                          <p class="text-xs text-red-700">
                            确认从社区删除「{{ c.title || c.videoUrl }}」？ 只删社区条目，<strong class="font-medium"
                              >不删这个用户的解析历史</strong
                            >—— 他在自己历史里仍然看得到。删除后紧跟地该链接的人再次解析会重新入库。
                          </p>
                          <div class="mt-2 flex items-center gap-2">
                            <button
                              type="button"
                              @click="confirmDeleteCommunity(c)"
                              :disabled="communityBusyId !== null"
                              class="px-3 py-1.5 rounded-lg bg-red-600 text-on-solid text-xs hover:bg-red-700 transition-colors disabled:opacity-50"
                            >
                              {{ communityBusyId === c.id ? '删除中…' : '确认删除' }}
                            </button>
                            <button
                              type="button"
                              @click="cancelDeleteCommunity"
                              class="px-3 py-1.5 rounded-lg bg-panel border border-line text-xs text-gray-500 hover:border-gray-300 transition-colors"
                            >
                              取消
                            </button>
                          </div>
                        </td>
                      </tr>
                    </template>
                  </tbody>
                </table>
              </div>
            </div>

            <!-- ══ AI 服务：可改的厂商 / 模型清单。凭据仍只在 .env（ADR 0010 / 0011）。
     清单行可改，但**不能新增**厂商：新增平台的凭据得先配 .env 并重启。 ══ -->
            <div v-else>
              <p class="mb-3 text-xs text-gray-400">
                可改显示名、端点、可选模型、平台默认与上下架。凭据（key）仍只在
                <code class="font-pixel">.env</code>，不经前端也不入库（ADR 0011）。
              </p>
              <div class="grid gap-3 sm:grid-cols-2">
                <div
                  v-for="m in view.items as AdminModelItem[]"
                  :key="m.id"
                  class="rounded-2xl bg-panel border border-line p-4"
                >
                  <div class="flex items-start justify-between gap-2">
                    <div class="min-w-0">
                      <h3 class="text-sm font-semibold text-gray-800">{{ m.label }}</h3>
                      <p class="mt-0.5 text-[11px] font-pixel text-gray-400 truncate">{{ m.id }}</p>
                    </div>
                    <span class="flex-shrink-0 flex gap-1.5">
                      <span
                        class="text-[10px] px-1.5 py-0.5 rounded border"
                        :class="
                          m.enabled
                            ? 'bg-emerald-50 text-emerald-600 border-emerald-200'
                            : 'bg-gray-100 text-gray-500 border-line'
                        "
                      >
                        {{ m.enabled ? '启用中' : '已停用' }}
                      </span>
                      <span
                        class="text-[10px] px-1.5 py-0.5 rounded border"
                        :class="
                          m.isReal
                            ? 'bg-cyan-50 text-cyan-600 border-cyan-200'
                            : 'bg-gray-100 text-gray-500 border-line'
                        "
                      >
                        {{ m.isReal ? '真实厂商' : '占位' }}
                      </span>
                    </span>
                  </div>
                  <p class="mt-2 text-xs text-gray-500">
                    默认模型：<span class="font-pixel">{{ m.defaultModel }}</span>
                  </p>
                  <div class="mt-2 flex flex-wrap gap-1.5">
                    <span
                      v-for="one in m.models"
                      :key="one"
                      class="text-[10px] font-pixel bg-panel-2 text-gray-600 border border-line px-1.5 py-0.5 rounded"
                      >{{ one }}</span
                    >
                  </div>
                  <p v-if="m.hint" class="mt-2 text-xs text-gray-400">{{ m.hint }}</p>

                  <div class="mt-3 flex items-center gap-2">
                    <button
                      type="button"
                      class="text-xs px-2.5 py-1 rounded-lg border border-line bg-panel-2 text-gray-600 hover:border-blue-200"
                      @click="openModelEditor(m.id)"
                    >
                      编辑
                    </button>
                  </div>

                  <div v-if="expandedModelId === m.id" class="mt-3 border-t border-line pt-3 space-y-2">
                    <div>
                      <label :for="`m-label-${m.id}`" class="block text-xs text-gray-500 mb-1">显示名</label>
                      <input
                        :id="`m-label-${m.id}`"
                        v-model="modelDrafts[m.id].label"
                        type="text"
                        class="w-full px-3 py-1.5 rounded-lg bg-panel border border-line text-sm text-gray-800"
                      />
                    </div>
                    <div>
                      <label :for="`m-hint-${m.id}`" class="block text-xs text-gray-500 mb-1">提示文案</label>
                      <input
                        :id="`m-hint-${m.id}`"
                        v-model="modelDrafts[m.id].hint"
                        type="text"
                        class="w-full px-3 py-1.5 rounded-lg bg-panel border border-line text-sm text-gray-800"
                      />
                    </div>
                    <div>
                      <label :for="`m-url-${m.id}`" class="block text-xs text-gray-500 mb-1">端点</label>
                      <input
                        :id="`m-url-${m.id}`"
                        v-model="modelDrafts[m.id].baseUrl"
                        type="text"
                        class="w-full px-3 py-1.5 rounded-lg bg-panel border border-line text-sm font-pixel text-gray-800"
                      />
                    </div>
                    <div v-if="hasModels(m)">
                      <label :for="`m-models-${m.id}`" class="block text-xs text-gray-500 mb-1"
                        >可选模型（逗号分隔）</label
                      >
                      <input
                        :id="`m-models-${m.id}`"
                        v-model="modelDrafts[m.id].modelsText"
                        type="text"
                        :aria-describedby="`m-modelhint-${m.id}`"
                        class="w-full px-3 py-1.5 rounded-lg bg-panel border border-line text-sm font-pixel text-gray-800"
                      />
                      <p :id="`m-modelhint-${m.id}`" class="mt-1 text-[11px] text-gray-400">平台默认必须从这里面选。</p>
                    </div>
                    <!-- 占位行（platform / custom）本来就没有可选模型。
                       渲染那两个输入框又禁止保存，只会把「编辑」变成死路。 -->
                    <p v-else class="text-[11px] text-gray-400">
                      这一行是占位，没有可选模型。可以改显示名、端点、上下架与排序； 要接真实厂商得在 .env
                      里配好凭据再重启（ADR 0011）。
                    </p>
                    <div class="flex flex-wrap gap-2">
                      <div v-if="hasModels(m)" class="min-w-40">
                        <label :for="`m-default-${m.id}`" class="block text-xs text-gray-500 mb-1">平台默认</label>
                        <select
                          :id="`m-default-${m.id}`"
                          v-model="modelDrafts[m.id].defaultModel"
                          class="w-full px-3 py-1.5 rounded-lg bg-panel border border-line text-sm font-pixel text-gray-800"
                        >
                          <option value="">（无）</option>
                          <option
                            v-for="one in (modelDrafts[m.id].modelsText || '')
                              .split(/[,，\n]/)
                              .map((s) => s.trim())
                              .filter(Boolean)"
                            :key="one"
                            :value="one"
                          >
                            {{ one }}
                          </option>
                        </select>
                      </div>
                      <div>
                        <label :for="`m-enabled-${m.id}`" class="block text-xs text-gray-500 mb-1">状态</label>
                        <select
                          :id="`m-enabled-${m.id}`"
                          v-model.number="modelDrafts[m.id].enabled"
                          class="px-3 py-1.5 rounded-lg bg-panel border border-line text-sm text-gray-800"
                        >
                          <option :value="1">上架</option>
                          <option :value="0">停用</option>
                        </select>
                      </div>
                      <div>
                        <label :for="`m-sort-${m.id}`" class="block text-xs text-gray-500 mb-1">排序</label>
                        <input
                          :id="`m-sort-${m.id}`"
                          v-model.number="modelDrafts[m.id].sortOrder"
                          type="number"
                          min="-1000"
                          max="1000"
                          class="w-24 px-3 py-1.5 rounded-lg bg-panel border border-line text-sm font-pixel text-gray-800"
                        />
                      </div>
                    </div>
                    <div class="flex items-center gap-2">
                      <button
                        type="button"
                        :disabled="savingModelId !== null"
                        class="text-xs px-3 py-1.5 rounded-lg bg-blue-600 text-on-primary disabled:opacity-50"
                        @click="saveModel(m)"
                      >
                        {{ savingModelId === m.id ? '保存中…' : '保存' }}
                      </button>
                      <button
                        type="button"
                        class="text-xs px-3 py-1.5 rounded-lg border border-line bg-panel-2 text-gray-600"
                        @click="closeModelEditor(m.id)"
                      >
                        取消
                      </button>
                      <span aria-live="polite" :class="['text-xs', feedbackClass(modelFeedbackOf(m.id).kind)]">{{
                        modelFeedbackOf(m.id).text
                      }}</span>
                    </div>
                  </div>
                </div>
              </div>
            </div>

            <!-- 翻页：三个分页页签共用。模型清单不分页，所以整块跳过。 -->
            <div v-if="tab !== 'models' && view.total > PAGE_SIZE" class="mt-6 flex items-center justify-between gap-3">
              <span class="text-xs text-gray-400 font-pixel"
                >第 {{ view.page }} / {{ totalPages }} 页 · 共 {{ view.total }} 条</span
              >
              <div class="flex items-center gap-2">
                <button
                  type="button"
                  @click="goPage(view.page - 1)"
                  :disabled="view.page <= 1 || view.loading"
                  class="px-3 py-1.5 rounded-lg bg-panel border border-line text-xs text-gray-500 hover:border-gray-300 transition-colors disabled:opacity-40"
                >
                  上一页
                </button>
                <button
                  type="button"
                  @click="goPage(view.page + 1)"
                  :disabled="view.page >= totalPages || view.loading"
                  class="px-3 py-1.5 rounded-lg bg-panel border border-line text-xs text-gray-500 hover:border-gray-300 transition-colors disabled:opacity-40"
                >
                  下一页
                </button>
              </div>
            </div>
          </template>
        </div>
      </template>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * 管理后台（工单 #14 / ADR 0010）。
 *
 * 边界，别越：
 *  · 写操作只有三类：**改额度**（用户页）、**账号生命周期**（建号 / 管理员标记 /
 *    删号，ADR 0012）、**社区审核**（改标签 / 删条目，ADR 0013）。封禁、解封、
 *    改 VIP、下架社区视频仍然不做（项目范围边界）。
 *  · 删社区条目只删 videos 那一行，不动用户的解析历史。
 *  · 视觉一律走 src/style.css 的 @theme 令牌，本组件里没有色值字面量，
 *    也没有 @theme 之外的颜色类（tests/admin-ui.test.mjs 守着这两条）。
 *  · 主题不在这里判定。data-theme 由 index.html 的内联脚本在首帧前写好，
 *    useTheme 只读 DOM；后台再判一次必然和那两处打架。
 */
import { ref, reactive, computed, onMounted } from 'vue'
import {
  fetchAdminUsers,
  setUserQuota,
  fetchAdminCommunity,
  fetchAdminModelCatalog,
  updateAdminModel,
  createAdminUser,
  setUserAdmin,
  deleteAdminUser,
  fetchTagVocabulary,
  updateCommunityTags,
  deleteCommunityVideo,
} from '../api/admin.ts'
// 类型单独一条 import：tests/admin-ui.test.mjs 冻结的是从 admin.ts 导入的
// **API 名字名单**，类型混进那一条 import 会被当成「多了一个没审的接口」。
import type { AdminUser, AdminCommunityItem, AdminModelItem, TagGroup } from '../api/admin.ts'

/** 三个页签的 key。既是状态槽位名，也是 panel 的 id 后缀。 */
type TabKey = 'users' | 'community' | 'models'

/** 三个页签里的行。users / community / models 三种行形状不同。 */
type Row = AdminUser | AdminCommunityItem | AdminModelItem

/**
 * 一个页签的视图状态。三个页签形状相同，只是装的行类型不同。
 *
 * 原来三个页签共用一个 `view.items` 槽位，行的类型三样混在一起，TS 只能把
 * `items` 推成 `never[]`，于是每一处 `view.items[i]` 都是错。拆成三个槽位之后，
 * 每个槽位只装自己那一种行，`view` 拿到的才是这三种的联合。
 */
interface ViewState<T> {
  items: T[]
  total: number
  page: number
  loading: boolean
  error: string
  forbidden: boolean
  touched: boolean
}

/** 行内反馈。用户行 / 社区行 / 厂商行 / 账号操作，形状一样。 */
interface Feedback {
  kind: string
  text: string
}

/** 额度草稿。`drafts[u.id]` 的写法是 v-model 只接受成员表达式逼出来的。 */
interface UserDraft {
  parse: string
  chat: string
}

/** 厂商行草稿。键是厂商 id（字符串），值是这一行可编辑的那几个字段。 */
interface ModelDraft {
  label: string
  hint: string
  baseUrl: string
  modelsText: string
  defaultModel: string
  enabled: number
  sortOrder: number
}

/** toModelList 的判定结果：ok 为真才带可用的模型名列表。 */
type ModelListResult = { ok: false; value: never[]; message: string } | { ok: true; value: string[]; message: string }

/** toQuotaValue 的判定结果：ok 为真才带额度（null 表示回落全局）。 */
type QuotaResult = { ok: true; value: number | null } | { ok: false; value: null; message: string }

/**
 * 下面有一批函数写成 `const fn: (…) => … = function fn(…) { … }`，签名是纯 JS。
 *
 * tests/admin-ui.test.mjs 会把这些函数的源码**原样抽出来**当 JavaScript 执行
 * （new Function），签名里只要带类型标注就会当场语法错。于是类型改从 const 的
 * 类型标注里来——抽出来的文本仍是 `function fn(x) { … }`，运行时行为一字未改。
 */

const emit = defineEmits(['back'])

const PAGE_SIZE = 20

/** 三个页签。key 同时是状态槽位名与 panel 的 id 后缀，顺序即展示顺序。
 *
 *  原来有第四个「额度」页签，它与用户页**同一个接口、同一份数据**，区别只是
 *  编辑控件常驻行内。用户页本来就能改额度，于是同一个动作有两个入口——
 *  两个入口意味着改了一处忘了另一处，两边就各说各话。删掉。
 *
 *  `as const` 让 key 保持字面量类型，于是页签 key 与状态槽位名共用 TabKey，
 *  不用两处各写一份字符串。 */
const TABS = [
  { key: 'users', label: '用户' },
  { key: 'community', label: '社区' },
  { key: 'models', label: 'AI 服务' },
] as const

/** 每个页签的空闲态文案。四份都在这里，空状态就不会只给某一页写。 */
const EMPTY = {
  users: { text: '没有匹配的用户', hint: '换个邮箱关键词，或清空搜索看全部注册用户' },
  community: { text: '社区还是空的', hint: '第一个解析视频的人会把它放进来' },
  models: { text: 'AI 服务清单是空的', hint: '厂商与模型由服务端配置下发' },
}

const tab = ref<TabKey>('users')
const query = ref('')

/**
 * 空页签状态。
 *
 * 默认类型参数取 never：三个槽位的调用点写法必须完全一样
 * （tests/admin-ui.test.mjs 按 `blankView()` 的字面量守着三份独立状态），
 * 槽位各自装哪一种行，由下面 `views` 那次整体标注给出。
 */
function blankView<T = never>(): ViewState<T> {
  return { items: [] as T[], total: 0, page: 1, loading: true, error: '', forbidden: false, touched: false }
}

/** 每个页签一份独立状态：切走再切回来列表还在，不必重打一次接口。
 *  拆成四份而不是一份共享的，是为了让「某个页签加载中」不会把
 *  另一个页签的内容一起换成骨架屏。
 *
 *  这里整体标一次类型：users 装 AdminUser、community 装 AdminCommunityItem、
 *  models 装 AdminModelItem。`as` 只改类型不改运行时对象——reactive 出来的
 *  还是那三个槽位，只是 TS 现在知道谁装谁。 */
const views = reactive({
  users: blankView(),
  community: blankView(),
  models: blankView(),
}) as {
  users: ViewState<AdminUser>
  community: ViewState<AdminCommunityItem>
  models: ViewState<AdminModelItem>
}

/**
 * 当前页签的那份状态。
 *
 * 类型取三种行的联合：脚本里只用它做与行类型无关的事（翻页、总数、增删行），
 * 需要具体行类型的地方直接用 `views.users.items` 那种精确槽位；模板里三处
 * v-for 各按自己的页签收窄。
 */
const view = computed<ViewState<Row>>(() => views[tab.value])
const tabLabel = computed(() => TABS.find((t) => t.key === tab.value)?.label || '')
const empty = computed(() => EMPTY[tab.value] || EMPTY.users)
const totalPages = computed(() => Math.max(1, Math.ceil(view.value.total / PAGE_SIZE)))

/**
 * snake_case → camelCase 的转换**不在这个文件里**。
 *
 * 三份转换（用户 / 社区 / 模型）都在 `api/admin.js`，列表出口在 api 层就已经翻好，
 * 组件拿到的直接就是 camelCase（工单 #15 收口）。
 *
 * 组件自己**不 import 任何 `to*` 转换函数**。这条是**通则**，不是逐个列名字：
 * 一旦组件再 import 转换函数，数据流里就有两种命名形态并存，而「读不到的键」
 * 不会报错——只会退化成 `undefined` 或默认值。工单 #15 实测过两例：
 * ① `enabled` 两种形态同名读得到，口径不一致时症状只是某几列显示异常；
 * ② `sort_order` 已翻成 `sortOrder`，再翻一次读不到就退回 0，于是
 *    **每保存一次就把该行排序号抹成 0**。两处单看都对，数据流上是错的。
 *
 * 列表出口在工单 #15 收口，回读出口在工单 #22 收口（`createAdminUser` /
 * `setUserAdmin` / `setUserQuota` / `updateCommunityTags` 四个都在 api 层翻好）。
 */

/**
 * 模型清单的 snake_case → camelCase 转换**不在这里**。
 *
 * 它归 `api/admin.js` 的 `toAdminModelItem`（工单 #15 收口：同端点曾经有两个
 * 出口，转换却散在组件里）。这里曾有一份重复的 `toModel`，与那份逐字段重复，
 * 且把 `enabled` 翻成布尔——而库表与接口口径都是 0 / 1 数字，两处不一致。
 * 组件直接用 api 层已转好的形状。
 */

/**
 * 额度草稿。
 *
 * `drafts[u.id]` 的写法是硬约束不是偷懒：Vue 的 v-model 只接受
 * 成员表达式，`draftOf(u.id).parse` 会在编译期直接报错。所以草稿必须
 * 在渲染之前就播种好——由 primeDrafts 在每次加载后负责。
 */
const drafts = reactive<Record<number, UserDraft>>({})

/**
 * 厂商行的草稿。与额度草稿同一套硬约束：`modelDrafts[m.id]` 的写法是
 * Vue v-model 只接受成员表达式逼出来的，必须在渲染前播种。
 * 由 primeModelDrafts 在每次 loadModels 之后负责。
 */
const modelDrafts = reactive<Record<string, ModelDraft>>({})
const expandedModelId = ref<string | null>(null)
const savingModelId = ref<string | null>(null)
const modelFeedbacks = reactive<Record<string, Feedback>>({})
const expandedId = ref<number | null>(null)
const savingId = ref<number | null>(null)
const feedbacks = reactive<Record<number, Feedback>>({})

function draftText(v: number | string | null | undefined): string {
  return v === null || v === undefined ? '' : String(v)
}

/** 把厂商行灌进草稿。渲染前必须先跑，否则 v-model 绑不到成员表达式。 */
function primeModelDrafts(items: AdminModelItem[]): void {
  for (const m of items) {
    modelDrafts[m.id] = {
      label: m.label || '',
      hint: m.hint || '',
      baseUrl: m.baseUrl || '',
      modelsText: (m.models || []).join(', '),
      defaultModel: m.defaultModel || '',
      enabled: m.enabled ? 1 : 0,
      sortOrder: m.sortOrder ?? 0,
    }
  }
}

function openModelEditor(id: string): void {
  expandedModelId.value = id
}

function closeModelEditor(id: string): void {
  if (expandedModelId.value === id) expandedModelId.value = null
}

/**
 * 这一行有没有可选模型。
 *
 * platform / custom 两个占位行的 models 就是空数组（后端播种如此），
 * 它们只能改显示名、端点、上下架与排序。判据用「有没有模型」而不是
 * 「是不是真实厂商」——前者是**这一行实际能不能存**的判据。
 */
const hasModels: (m: AdminModelItem) => boolean = function hasModels(m) {
  return Array.isArray(m.models) && m.models.length > 0
}

function modelFeedbackOf(id: string): Feedback {
  if (!modelFeedbacks[id]) modelFeedbacks[id] = { kind: '', text: '' }
  return modelFeedbacks[id]
}

/**
 * 模型列表文本 → 数组。
 *
 * 重复项在这里就拦掉，不发到服务端：后端会回 400，但那条错误信息讲的是
 * 「后端的规则」，而用户刚做的是「把两个一样的名字删了一个」。
 */
const toModelList: (text: string) => ModelListResult = function toModelList(text) {
  const parts = String(text || '')
    .split(/[,，\n]/)
    .map((s) => s.trim())
    .filter(Boolean)
  if (parts.length === 0) {
    return { ok: false, value: [], message: '至少要留一个可选模型' }
  }
  const dupes = parts.filter((p, i) => parts.indexOf(p) !== i)
  if (dupes.length > 0) {
    return { ok: false, value: [], message: `模型名重复了：${[...new Set(dupes)].join('、')}` }
  }
  return { ok: true, value: parts, message: '' }
}

/** 用服务端回读的那份替换本地行：成功提示与卡片显示必须是同一份。 */
function replaceModel(item: AdminModelItem): void {
  if (!item) return
  // `updateAdminModel` 已经在 api 层把回读**原文**转成 camelCase 了
  //（`api/admin.js:190`），所以这里直接用，不要再翻一次。
  // 翻第二次不会报错——`sort_order` 已经变成 `sortOrder`，`toAdminModelItem`
  // 读不到就退回 0，于是**每保存一次就把排序号抹成 0**。
  const list = views.models.items
  const i = list.findIndex((m) => m.id === item.id)
  if (i >= 0) list.splice(i, 1, item)
  primeModelDrafts([item])
}

/**
 * 保存厂商行。
 *
 * 与 saveQuota 同一纪律：**不做乐观更新**。先本地改卡片、请求失败时静默
 * 回滚，管理员看到的是「改成功了」而实际没改 —— 那比报错更坏。
 * 成功后也不自动收起编辑行：收起只由「取消」负责，否则反馈会跟着一起消失。
 */
const saveModel: (m: AdminModelItem) => Promise<void> = async function saveModel(m) {
  if (savingModelId.value !== null) return
  const d = modelDrafts[m.id] || {}
  const patch = {
    label: d.label,
    hint: d.hint,
    baseUrl: d.baseUrl,
    enabled: d.enabled === 1,
    sortOrder: Number(d.sortOrder),
  }
  // 只有真的有可选模型时才碰这两列。占位行走 else 分支：一个字段都不发，
  // 而不是发一个空数组 —— 后端拒空 models，硬发会让这一行彻底存不了。
  if (hasModels(m)) {
    const list = toModelList(d.modelsText)
    if (!list.ok) {
      modelFeedbackOf(m.id).kind = 'error'
      modelFeedbackOf(m.id).text = `可选模型：${list.message}`
      return
    }
    if (list.value.indexOf(d.defaultModel) < 0) {
      modelFeedbackOf(m.id).kind = 'error'
      modelFeedbackOf(m.id).text = '平台默认模型必须从上面的可选模型里选'
      return
    }
    // 这两列原来直接往 patch 上挂。patch 的类型是从字面量推出来的，push 不进去，
    // 所以改成 Object.assign：同一个对象、同一组键、同一份值，运行时一致。
    Object.assign(patch, { models: list.value, defaultModel: d.defaultModel })
  }

  savingModelId.value = m.id
  modelFeedbackOf(m.id).kind = ''
  modelFeedbackOf(m.id).text = ''
  try {
    const res = await updateAdminModel(m.id, patch)
    replaceModel(res.item)
    modelFeedbackOf(m.id).kind = 'ok'
    modelFeedbackOf(m.id).text = res.platformDefault
      ? `已保存。该厂商的平台默认模型现在是 ${res.platformDefault}。`
      : '已保存。该厂商当前没有平台默认模型（已停用，或没配默认）。'
  } catch (err) {
    modelFeedbackOf(m.id).kind = 'error'
    modelFeedbackOf(m.id).text = `没改成：${messageOf(err)}`
  } finally {
    savingModelId.value = null
  }
}

function primeDrafts(users: AdminUser[]): void {
  for (const u of users) {
    drafts[u.id] = { parse: draftText(u.parseLimitOverride), chat: draftText(u.chatLimitOverride) }
  }
}

/** 行是否展开。**同一时刻只有一行**（expandedId 是单值，不是集合）。
 *
 * 原来这一行是 `tab === 'quota' || expandedId === u.id`：额度页时永远为真，
 * 于是那一页上每一行的编辑器都常驻，页面上十几行输入框一起躺着。额度页
 * 删掉之后这里只剩单开这一条语义——展开是**逐行**的动作，不该是整页的状态。
 */
function editorOpen(u: AdminUser): boolean {
  return expandedId.value === u.id
}

/** 点该行展开/收起。点谁开谁，别的一行自动收起。 */
const toggleRow: (u: AdminUser) => void = function toggleRow(u) {
  expandedId.value = expandedId.value === u.id ? null : u.id
}

function closeEditor(id: number): void {
  if (expandedId.value === id) expandedId.value = null
}

function feedbackOf(id: number): Feedback {
  if (!feedbacks[id]) feedbacks[id] = { kind: '', text: '' }
  return feedbacks[id]
}

function feedbackClass(kind: string): string {
  if (kind === 'ok') return 'text-emerald-600'
  if (kind === 'warn') return 'text-amber-600'
  return 'text-red-600'
}

/** 「用了 / 上限」一行。-1 是无限；来源标签让管理员知道这值是谁给的。 */
function counter(used: number, limit: number, source: string): string {
  const shown = limit === -1 ? '∞' : limit
  return `${used} / ${shown} · ${source || 'global'}`
}

/** 错误对象是不是一个「键值读得到」的对象。axios 的错误、我们自己造的都算。 */
function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null
}

/** 从错误里取服务端给的 detail（后端 400/409 的那句话）。 */
function errorDetail(err: unknown): unknown {
  if (!isRecord(err)) return undefined
  const response = err.response
  if (!isRecord(response)) return undefined
  const data = response.data
  if (!isRecord(data)) return undefined
  return data.detail
}

/** 从错误里取 HTTP 状态码。取不到按 0 算（非 403 即「普通失败」）。 */
function errorStatus(err: unknown): number {
  if (!isRecord(err)) return 0
  const response = err.response
  if (!isRecord(response)) return 0
  const status = response.status
  return typeof status === 'number' ? status : 0
}

function messageOf(err: unknown): string {
  const detail = errorDetail(err)
  if (typeof detail === 'string' && detail) return detail
  const message = isRecord(err) ? err.message : undefined
  // 原实现是 `(err && err.message) || '请求失败'`：err.message 不是字符串时原样返回。
  // 这里用 as 保持同一行为——err 以前是 any，TS 从来没管过这一支。
  return (message || '请求失败') as string
}

/**
 * 失败必须分两种形态，不能都退化成空列表：
 *  · 403 = 不是管理员，整个后台都不该给他看 → 整页可读错误态
 *  · 其它 = 这次请求挂了 → 列表清空 + 可重试，别渲染成「没有数据」
 */
const markFailure: <T>(v: ViewState<T>, err: unknown) => void = function markFailure(v, err) {
  const status = errorStatus(err)
  v.forbidden = status === 403
  v.error = v.forbidden ? '' : messageOf(err)
  v.items = []
  v.total = 0
}

const loadUsers: () => Promise<void> = async function loadUsers() {
  const v = views.users
  v.loading = true
  v.error = ''
  v.forbidden = false
  try {
    const res = await fetchAdminUsers({ page: v.page, pageSize: PAGE_SIZE, q: query.value.trim() })
    v.items = res.items || []
    v.total = res.total || 0
    v.page = res.page || v.page
    primeDrafts(v.items)
  } catch (err) {
    markFailure(v, err)
  } finally {
    v.loading = false
  }
}

const loadCommunity: () => Promise<void> = async function loadCommunity() {
  const v = views.community
  v.loading = true
  v.error = ''
  v.forbidden = false
  try {
    const res = await fetchAdminCommunity({ page: v.page, pageSize: PAGE_SIZE })
    v.items = res.items || []
    v.total = res.total || 0
    v.page = res.page || v.page
  } catch (err) {
    markFailure(v, err)
  } finally {
    v.loading = false
  }
}

const loadModels: () => Promise<void> = async function loadModels() {
  const v = views.models
  v.loading = true
  v.error = ''
  v.forbidden = false
  try {
    const res = await fetchAdminModelCatalog()
    v.items = res.items || []
    v.total = v.items.length
    primeModelDrafts(v.items)
  } catch (err) {
    markFailure(v, err)
  } finally {
    v.loading = false
  }
}

// ── 社区审核（ADR 0013）───────────────────────────────────────────────
//
// 反馈不复用 feedbackOf（那个渲染在用户的展开行里），也不复用 opsFeedbackOf
// （那个是用户主行的）。三个 state 各有位置，借用会让它们互相顶掉。
const expandedCommunityId = ref<number | null>(null)
const communityBusyId = ref<number | null>(null)
const pendingCommunityDeleteId = ref<number | null>(null)
// 0 是「还没有上限」，不是兜底常量：加载态与出错态下这块模板走
// v-else 不渲染，所以 0 不会被看见（工单 #41）。
//
// 类型靠 `[] as TagGroup[]` 给出来：写成 `ref<{...}>({...})` 会把这句里的
// `ref({ maxTags: 0` 改掉，而 tests/admin-ui.test.mjs 正按这串字断言初值是 0。
const vocabulary = ref({ maxTags: 0, groups: [] as TagGroup[] })
const vocabLoading = ref(false)
const vocabError = ref('')
const tagDrafts = reactive<Record<number, string[]>>({})
const communityFeedbacks = reactive<Record<number, Feedback>>({})

/**
 * 上限由服务端给，前端不自己数，也不设兜底常量：兜底一旦与服务端
 * 悄悄分叉，界面会按旧数字放行、而后端按新数字 400（工单 #41）。
 */
const maxTags = computed(() => vocabulary.value.maxTags)

function communityFeedbackOf(id: number): Feedback {
  if (!communityFeedbacks[id]) communityFeedbacks[id] = { kind: '', text: '' }
  return communityFeedbacks[id]
}

function communityEditorOpen(id: number): boolean {
  return expandedCommunityId.value === id
}

/**
 * 词表只取一次，且在确实有人要改标签时才取。
 *
 * 不跟社区列表一起取：词表取失败不应该连块整页一起报错——管理员只是想看看列表。
 */
async function ensureVocabulary(): Promise<void> {
  if (vocabulary.value.groups.length || vocabLoading.value) return
  vocabLoading.value = true
  vocabError.value = ''
  try {
    vocabulary.value = await fetchTagVocabulary()
  } catch (err) {
    vocabError.value = `取不到标签词表，改不了标签：${messageOf(err)}`
  } finally {
    vocabLoading.value = false
  }
}

/**
 * 点行展开/收起。同一时刻只有一行。
 *
 * 草稿每次从**库里那个值**起头，不从上一次改到一半的那份继续：
 * 否则取消一次再点进来，看到的是上次的临时值而不是库里的。
 */
function toggleCommunityRow(c: AdminCommunityItem): void {
  const next = expandedCommunityId.value === c.id ? null : c.id
  expandedCommunityId.value = next
  if (next === null) return
  ensureVocabulary()
  tagDrafts[c.id] = Array.isArray(c.tags) ? c.tags.slice() : []
  communityFeedbackOf(c.id).kind = ''
  communityFeedbackOf(c.id).text = ''
}

function closeCommunityEditor(id: number): void {
  if (expandedCommunityId.value === id) expandedCommunityId.value = null
}

const selectedTags: (id: number) => string[] = function selectedTags(id) {
  return tagDrafts[id] || []
}

const tagSelected: (id: number, tag: string) => boolean = function tagSelected(id, tag) {
  return selectedTags(id).indexOf(tag) >= 0
}

const selectedTagCount: (id: number) => number = function selectedTagCount(id) {
  return selectedTags(id).length
}

const toggleTag: (id: number, tag: string) => void = function toggleTag(id, tag) {
  const cur = selectedTags(id).slice()
  const i = cur.indexOf(tag)
  if (i >= 0) {
    cur.splice(i, 1)
  } else {
    if (cur.length >= maxTags.value) return
    cur.push(tag)
  }
  tagDrafts[id] = cur
}

/** 改标签。不做乐观更新：列表里的标签一律等服务端回读结果再替。 */
async function saveCommunityTags(c: AdminCommunityItem): Promise<void> {
  if (communityBusyId.value !== null) return
  const picked = selectedTags(c.id)
  if (!picked.length) {
    communityFeedbackOf(c.id).kind = 'error'
    communityFeedbackOf(c.id).text = '至少留一个标签——每条视频都得能归类。'
    return
  }
  communityBusyId.value = c.id
  communityFeedbackOf(c.id).kind = ''
  communityFeedbackOf(c.id).text = ''
  try {
    const res = await updateCommunityTags(c.id, picked)
    replaceCommunityItem(res.item)
    communityFeedbackOf(c.id).kind = 'ok'
    communityFeedbackOf(c.id).text = '标签已更新'
  } catch (err) {
    communityFeedbackOf(c.id).kind = 'error'
    communityFeedbackOf(c.id).text = `标签没改成：${messageOf(err)}`
  } finally {
    communityBusyId.value = null
  }
}

/** 用服务端回读的那份替换本地行：两个页签看到的都是同一份真实值。 */
function replaceCommunityItem(item: AdminCommunityItem): void {
  if (!item) return
  const list = views.community.items
  const i = list.findIndex((c) => c.id === item.id)
  if (i >= 0) {
    list.splice(i, 1, item)
    tagDrafts[item.id] = item.tags.slice()
  }
}

function askDeleteCommunity(c: AdminCommunityItem): void {
  pendingCommunityDeleteId.value = c.id
  communityFeedbackOf(c.id).kind = ''
  communityFeedbackOf(c.id).text = ''
}

function cancelDeleteCommunity() {
  pendingCommunityDeleteId.value = null
}

/**
 * 删条目。只删 videos 那一行，解析历史不动。
 *
 * 成功后不留反馈：行已经从列表里抹掉，反馈写在那行上就看不到了。
 */
async function confirmDeleteCommunity(c: AdminCommunityItem): Promise<void> {
  if (communityBusyId.value !== null) return
  communityBusyId.value = c.id
  communityFeedbackOf(c.id).kind = ''
  communityFeedbackOf(c.id).text = ''
  try {
    await deleteCommunityVideo(c.id)
    const list = views.community.items
    const i = list.findIndex((x) => x.id === c.id)
    if (i >= 0) list.splice(i, 1)
    views.community.total = Math.max(0, views.community.total - 1)
    pendingCommunityDeleteId.value = null
    if (expandedCommunityId.value === c.id) expandedCommunityId.value = null
  } catch (err) {
    communityFeedbackOf(c.id).kind = 'error'
    communityFeedbackOf(c.id).text = `没删掉：${messageOf(err)}`
  } finally {
    communityBusyId.value = null
  }
}

/** 页签 → 取数函数。首次进入才打接口，切回来用已加载的那份。 */
const LOADERS = {
  users: loadUsers,
  community: loadCommunity,
  models: loadModels,
}

function selectTab(key: TabKey): void {
  if (!LOADERS[key]) return
  tab.value = key
  const v = views[key]
  if (!v.touched) {
    v.touched = true
    LOADERS[key]()
  }
}

function reload(): void {
  const v = view.value
  v.touched = true
  LOADERS[tab.value]()
}

function search(): void {
  view.value.page = 1
  reload()
}

function clearQuery(): void {
  query.value = ''
  search()
}

function goPage(n: number): void {
  const v = view.value
  if (n < 1 || n > totalPages.value) return
  v.page = n
  reload()
}

/** 只允许契约里的三个取值：null 清除覆盖回落全局、0 一条都不能用、-1 无限。 */
const toQuotaValue: (text: string | null | undefined) => QuotaResult = function toQuotaValue(text) {
  const raw = String(text === null || text === undefined ? '' : text).trim()
  if (raw === '') return { ok: true, value: null }
  const n = Number(raw)
  if (!Number.isInteger(n) || n < -1) {
    return { ok: false, value: null, message: '额度只能是 -1（无限）、0（停用）或正整数' }
  }
  return { ok: true, value: n }
}

/** 用服务端回读的那份替换本地行：两个页签看到的都是同一份真实值。 */
function replaceUser(user: AdminUser): void {
  if (!user) return
  const list = views.users.items
  const i = list.findIndex((u) => u.id === user.id)
  if (i >= 0) list.splice(i, 1, user)
  drafts[user.id] = { parse: draftText(user.parseLimitOverride), chat: draftText(user.chatLimitOverride) }
}

/**
 * 改额度。
 *
 * 成功与失败都要有反馈，且**不做乐观更新**：列表里的数字一律等
 * 服务端重读库的结果再替换（契约里 200 就带 user）。先本地改数字、
 * 请求失败时静默回滚——管理员看到的是「改成功了」而实际没改，
 * 这是比报错更坏的一种。
 */
async function saveQuota(u: AdminUser): Promise<void> {
  if (savingId.value !== null) return
  const draft = drafts[u.id] || { parse: '', chat: '' }
  const parseLimit = toQuotaValue(draft.parse)
  const chatLimit = toQuotaValue(draft.chat)
  if (!parseLimit.ok) {
    feedbackOf(u.id).kind = 'error'
    feedbackOf(u.id).text = `解析上限：${parseLimit.message}`
    return
  }
  if (!chatLimit.ok) {
    feedbackOf(u.id).kind = 'error'
    feedbackOf(u.id).text = `追问上限：${chatLimit.message}`
    return
  }

  savingId.value = u.id
  feedbackOf(u.id).kind = ''
  feedbackOf(u.id).text = ''
  try {
    const res = await setUserQuota(u.id, { parseLimit: parseLimit.value, chatLimit: chatLimit.value })
    replaceUser(res.user)
    if (res.note === 'vip_not_effective') {
      // 管理员给有效 VIP 改额度不会有任何效果（服务端在解析上限之前
      // 就对 VIP 短路返回无限）。不说的话，管理员只会以为自己操作错了。
      feedbackOf(u.id).kind = 'warn'
      feedbackOf(u.id).text = res.message || '已保存，但给有效 VIP 改额度不会生效'
    } else {
      feedbackOf(u.id).kind = 'ok'
      feedbackOf(u.id).text = '额度已更新'
    }
    // 成功**不**收起编辑行。反馈就写在这行里，收起来等于把「改成功了」
    // 一起收走——管理员会以为自己没点上。收起只由「取消」按钮负责。
  } catch (err) {
    feedbackOf(u.id).kind = 'error'
    feedbackOf(u.id).text = `额度没改成：${messageOf(err)}`
  } finally {
    savingId.value = null
  }
}

// ── 账号生命周期（ADR 0012）────────────────────────────────
//
// 反馈**不复用** feedbackOf：额度编辑器的反馈渲染在展开行里，
// 账号操作的反馈渲染在主行里。同一个 state 会让两条反馈互相顶掉。
const createOpen = ref(false)
const creating = ref(false)
const busyUserId = ref<number | null>(null)
const pendingDeleteId = ref<number | null>(null)
const createDraft = reactive({ email: '', password: '', isAdmin: false })
const createFeedback = reactive({ kind: '', text: '' })
const opsFeedbacks = reactive<Record<number, Feedback>>({})

function opsFeedbackOf(id: number): Feedback {
  if (!opsFeedbacks[id]) opsFeedbacks[id] = { kind: '', text: '' }
  return opsFeedbacks[id]
}

function toggleCreate(): void {
  createOpen.value = !createOpen.value
  createFeedback.kind = ''
  createFeedback.text = ''
}

/** 建号。空邮箱 / 短口令在**本地**就挡掉，不去跑一趟网络。 */
const saveNewUser: () => Promise<void> = async function saveNewUser() {
  if (creating.value) return
  const email = createDraft.email.trim()
  const password = createDraft.password
  if (!email) {
    createFeedback.kind = 'error'
    createFeedback.text = '邮箱不能为空'
    return
  }
  if (password.length < 6) {
    createFeedback.kind = 'error'
    createFeedback.text = '初始密码至少 6 位'
    return
  }
  creating.value = true
  createFeedback.kind = ''
  createFeedback.text = ''
  try {
    const res = await createAdminUser({ email, password, isAdmin: createDraft.isAdmin })
    const fresh = res.user
    // 列表按 id 升序，新号在末尾。直接 push 再排序，别指望后端重排。
    //
    // 这里仍然是 view.value 而不是 views.users.items：建号面板只存在于「用户」
    // 页签，两者指的是同一份槽位，行为一致；改成 views.users.items 的话，
    // tests/admin-ui.test.mjs 会把这函数原样抽出来当 JavaScript 跑，而沙箱里
    // 只注入了 view，没有 views，会当场 ReferenceError。
    view.value.items.push(fresh)
    view.value.total += 1
    createDraft.email = ''
    createDraft.password = ''
    createDraft.isAdmin = false
    createOpen.value = false
    createFeedback.kind = 'ok'
    createFeedback.text = `已创建 ${fresh.email}。初始密码由你转交，用户当前无法自行改密。`
  } catch (err) {
    createFeedback.kind = 'error'
    createFeedback.text = `没建成：${messageOf(err)}`
  } finally {
    creating.value = false
  }
}

/** 提权 / 撤权。成功后用**回读**替换本地行，不做乐观更新。 */
const toggleAdmin: (u: AdminUser) => Promise<void> = async function toggleAdmin(u) {
  if (busyUserId.value !== null) return
  busyUserId.value = u.id
  opsFeedbackOf(u.id).kind = ''
  opsFeedbackOf(u.id).text = ''
  try {
    const res = await setUserAdmin(u.id, !u.isAdmin)
    replaceUser(res.user)
    opsFeedbackOf(u.id).kind = 'ok'
    opsFeedbackOf(u.id).text = res.user.isAdmin ? `${u.email} 现在是管理员` : `已撤销 ${u.email} 的管理员权限`
  } catch (err) {
    opsFeedbackOf(u.id).kind = 'error'
    opsFeedbackOf(u.id).text = `没改成功：${messageOf(err)}`
  } finally {
    busyUserId.value = null
  }
}

/**
 * 删除走**两步**，不用 window.confirm。
 *
 * confirm() 阻塞整个事件循环、样式无法统一、在自动化里也没法断言；
 * 内联二次确认既是同一个可测的状态机，也不会在误点时把用户卡住。
 */
const askDelete: (u: AdminUser) => void = function askDelete(u) {
  pendingDeleteId.value = u.id
  opsFeedbackOf(u.id).kind = ''
  opsFeedbackOf(u.id).text = ''
}

function cancelDelete() {
  pendingDeleteId.value = null
}

const confirmDelete: (u: AdminUser) => Promise<void> = async function confirmDelete(u) {
  if (busyUserId.value !== null) return
  busyUserId.value = u.id
  opsFeedbackOf(u.id).kind = ''
  opsFeedbackOf(u.id).text = ''
  try {
    await deleteAdminUser(u.id)
    // 同 saveNewUser：删号面板只在「用户」页签里，view.value 与 views.users.items
    // 是同一份槽位；这里保持 view.value 是因为测试沙箱只注入了 view。
    const i = view.value.items.findIndex((x) => x.id === u.id)
    if (i >= 0) view.value.items.splice(i, 1)
    view.value.total = Math.max(0, view.value.total - 1)
    pendingDeleteId.value = null
  } catch (err) {
    // 409 = 名下还有内容。blockers 是**数字**，别从中文里正则抠。
    // 这段被 tests/admin-ui.test.mjs 原样抽出来当 JavaScript 跑（沙箱里只给了
    // view 这几个自由变量），所以只能写纯 JS：收窄全靠 typeof + in，
    // 上面那套类型守卫在沙箱里并不存在，用不了。
    const resp = typeof err === 'object' && err !== null && 'response' in err ? err.response : null
    const data = typeof resp === 'object' && resp !== null && 'data' in resp ? resp.data : null
    const blockers = typeof data === 'object' && data !== null && 'blockers' in data ? data.blockers : null
    const orders = typeof blockers === 'object' && blockers !== null && 'orders' in blockers ? blockers.orders : 0
    const history =
      typeof blockers === 'object' && blockers !== null && 'parse_history' in blockers ? blockers.parse_history : 0
    const extra =
      blockers && typeof blockers === 'object' && Object.keys(blockers).length
        ? `（订单 ${orders || 0} 条、解析历史 ${history || 0} 条）`
        : ''
    opsFeedbackOf(u.id).kind = 'error'
    opsFeedbackOf(u.id).text = `没删掉：${messageOf(err)}${extra}`
  } finally {
    busyUserId.value = null
  }
}

/** 方向键在页签间移动。Tab 键本来就能逐个走到，这里只是多一条捷径。 */
function onTabKeydown(key: TabKey, ev: KeyboardEvent): void {
  const i = TABS.findIndex((t) => t.key === key)
  if (i < 0) return
  let next: { key: TabKey; label: string } | null = null
  if (ev.key === 'ArrowRight') next = TABS[(i + 1) % TABS.length]
  else if (ev.key === 'ArrowLeft') next = TABS[(i - 1 + TABS.length) % TABS.length]
  else if (ev.key === 'Home') next = TABS[0]
  else if (ev.key === 'End') next = TABS[TABS.length - 1]
  if (!next) return
  ev.preventDefault()
  selectTab(next.key)
  const el = document.getElementById(`admin-tab-${next.key}`)
  if (el) el.focus()
}

onMounted(() => {
  views.users.touched = true
  loadUsers()
})
</script>
