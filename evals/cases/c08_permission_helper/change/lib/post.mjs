function canEdit(user, post) {
  return user.id === post.ownerId || user.role === "admin";
}

export function editPost(user, post, title) {
  if (!canEdit(user, post)) throw new Error("forbidden");
  return { ...post, title };
}
