// Steps through the native input, so step size, step base and limits work exactly as the arrow keys do,
// and the new value reaches the server the same way as typing.
export default {
  template: `
    <q-input ref="field" v-bind="$attrs">
      <template v-for="(_, slot) in $slots" v-slot:[slot]="slotProps">
        <slot :name="slot" v-bind="slotProps || {}" />
      </template>
      <template v-if="!$slots.append" v-slot:append>
        <div class="vi-stepper" @pointerdown.prevent>
          <button v-for="dir in [1, -1]" :key="dir" type="button" tabindex="-1"
                  :aria-label="dir > 0 ? 'Increase' : 'Decrease'"
                  @pointerdown="hold(dir, $event)" @pointerup="stop" @pointerleave="stop" @pointercancel="stop">
            <svg viewBox="0 0 10 6" width="9" height="6" aria-hidden="true">
              <path :d="dir > 0 ? 'M1 5l4-4 4 4' : 'M1 1l4 4 4-4'" fill="none" stroke="currentColor"
                    stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" />
            </svg>
          </button>
        </div>
      </template>
    </q-input>
  `,
  inheritAttrs: false,
  beforeUnmount() {
    this.stop();
  },
  methods: {
    step(dir) {
      const input = this.$refs.field?.getNativeElement();
      if (!input || input.disabled || input.readOnly) return;
      try {
        dir > 0 ? input.stepUp() : input.stepDown();
      } catch (e) {
        return; // step="any" can't step
      }
      input.dispatchEvent(new Event("input", { bubbles: true }));
    },
    hold(dir, event) {
      if (event.button !== 0) return;
      this.stop();
      this.step(dir);
      const repeat = (delay) => {
        this.timer = setTimeout(() => {
          this.step(dir);
          repeat(70);
        }, delay);
      };
      repeat(400);
      window.addEventListener("blur", this.stop, { once: true });
    },
    stop() {
      clearTimeout(this.timer);
      window.removeEventListener("blur", this.stop);
    },
  },
};
