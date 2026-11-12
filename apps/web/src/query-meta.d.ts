import '@tanstack/react-query';

declare module '@tanstack/react-query' {
  interface Register {
    /** `errorToast: false` opts a mutation out of the global failure toast (inline error). */
    mutationMeta: { errorToast?: boolean };
  }
}
